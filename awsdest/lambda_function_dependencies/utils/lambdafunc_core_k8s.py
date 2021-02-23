from os import path
from typing import Any, Union

import yaml
from kubernetes import client, config, utils
import boto3
import string
import random
import base64
from botocore.signers import RequestSigner
import re

from utils.lambdafunc_core_aws import *
import time


############

def _get_bearer_token(cluster_id, region):
    # print("awsconfig in get bearer:", awsconfig)
    STS_TOKEN_EXPIRES_IN = 60

    session = boto3.session.Session(region_name=region)
    client = session.client('sts')
    service_id = client.meta.service_model.service_id

    signer = RequestSigner(
        service_id,
        region,
        'sts',
        'v4',
        session.get_credentials(),
        session.events
    )

    params = {
        'method': 'GET',
        'url': 'https://sts.{}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15'.format(region),
        'body': {},
        'headers': {
            'x-k8s-aws-id': cluster_id
        },
        'context': {}
    }

    signed_url = signer.generate_presigned_url(
        params,
        region_name=region,
        expires_in=STS_TOKEN_EXPIRES_IN,
        operation_name=''
    )

    # print("signed URL:", signed_url)
    base64_url = base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8')
    # remove any base64 encoding padding:
    return 'k8s-aws-v1.' + re.sub(r'=*', '', base64_url)


############

def _create_kube_config(clustername, aws_region, KUBE_FILEPATH):
    try:
        eks_client = boto3.client('eks', region_name=aws_region)
        response = eks_client.describe_cluster(name=clustername)
    except ClientError as e:
        print("Unexpected error while describing cluster : %s" % e)
        return False

    k8s_cert = response["cluster"]["certificateAuthority"]["data"]
    k8s_ep = response["cluster"]["endpoint"]
    # Generating kubeconfig
    kube_content = dict()
    kube_content['apiVersion'] = 'v1'
    kube_content['clusters'] = [
        {
            'cluster':
                {
                    'server': k8s_ep,
                    'certificate-authority-data': k8s_cert
                },
            'name': 'kubernetes'

        }]

    kube_content['contexts'] = [
        {
            'context':
                {
                    'cluster': 'kubernetes',
                    'user': 'aws'
                },
            'name': 'aws'
        }]

    kube_content['current-context'] = 'aws'
    kube_content['Kind'] = 'config'
    kube_content['users'] = [
        {
            'name': 'aws',
            'user':
                {
                    'name': 'who-cares'
                }
        }]
    # Write kubeconfig
    with open(KUBE_FILEPATH, 'w') as outfile:
        yaml.dump(kube_content, outfile, default_flow_style=False)
    return True


def _create_deployment_object(DEPLOYMENT_NAME, model_imagename_ecrpath, replicas_for_app):
    container = client.V1Container(
        name=DEPLOYMENT_NAME,
        image=model_imagename_ecrpath,
        ports=[client.V1ContainerPort(container_port=8080)])
    # Create and configurate a spec section
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": DEPLOYMENT_NAME}),
        spec=client.V1PodSpec(containers=[container]))
    # Create the specification of deployment
    spec = client.V1DeploymentSpec(
        replicas=replicas_for_app,
        template=template,
        selector={'matchLabels': {'app': DEPLOYMENT_NAME}})
    # Instantiate the deployment object
    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=DEPLOYMENT_NAME),
        spec=spec)

    return deployment


def _create_service_body(k8s_service_name, k8s_deployment_name):
    core_v1_api = client.CoreV1Api()
    body = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(
            name=k8s_service_name
        ),
        spec=client.V1ServiceSpec(
            selector={"app": k8s_deployment_name},
            ports=[client.V1ServicePort(
                port=8080,
                target_port=8080
            )]
        )
    )
    return body


def _create_ingress_body(k8s_ingress_name, k8s_service_name, unique_env_id):
    # we have to have following rewrites else there will be ingress conflicts between multiple pods.
    rewrite_target = "/$2"
    body = client.NetworkingV1beta1Ingress(
        api_version="networking.k8s.io/v1beta1",
        kind="Ingress",
        metadata=client.V1ObjectMeta(name=k8s_ingress_name, annotations={
            "nginx.ingress.kubernetes.io/rewrite-target": rewrite_target
        }),
        spec=client.NetworkingV1beta1IngressSpec(
            rules=[client.NetworkingV1beta1IngressRule(
                host="",
                http=client.NetworkingV1beta1HTTPIngressRuleValue(
                    paths=[client.NetworkingV1beta1HTTPIngressPath(
                        path="/" + unique_env_id + "(/|$)(.*)",
                        backend=client.NetworkingV1beta1IngressBackend(
                            service_port=8080,
                            service_name=k8s_service_name)

                    )]
                )
            )
            ]
        )
    )
    return body


def _wait_for_deployment_complete(deployment_name, replicas, timeout, k8s_api_v1_client, k8s_namespace):
    start = time.time()
    while (time.time() - start) < timeout:
        time.sleep(5)
        try:
            api_response = k8s_api_v1_client.read_namespaced_deployment(name=deployment_name, namespace=k8s_namespace)
            if api_response.status.available_replicas == replicas:
                return True
            else:
                return False
        except:
            return False
    raise RuntimeError(f'waiting timeout for deployment {deployment_name}')


def create_deployment_for_model(model_imagename, clustername, k8s_namespace, aws_region, unique_env_id):
    # keep following name in mind as this is the selector and used across the board.
    # Referred again in "service.yaml" and loadbabalncer.yaml object creation

    DEPLOYMENT_NAME = "scoringsasmm-" + unique_env_id
    k8s_service_name = "scoringsassvc-" + unique_env_id
    k8s_ingress_name = "scoringsasing-" + unique_env_id
    k8s_kubefilepath = "/tmp/tmp_kubeconfig"

    k8s_pods_creation_timeout = 120
    replicas = 1

    try:
        ecr_client = boto3.client('ecr', region_name=aws_region)
        response = ecr_client.describe_images(repositoryName=model_imagename)
        model_imagename_ecrpath = response['imageDetails'][0][
                                      'registryId'] + ".dkr.ecr." + aws_region + ".amazonaws.com/" + model_imagename + ":latest"
        if (response['ResponseMetadata']['HTTPStatusCode'] != 200):
            print("Image not found or something else wrong with repo")
            return (310, "Unexpected error getting model ")
    except ClientError as e:
        print("Unexpected error while trying to check on Model image presence: %s" % e)
        return (320, "Unexpected error getting model" + str(e))

    if not _create_kube_config(clustername, aws_region, k8s_kubefilepath):
        print("Kube config creation failed:")
        return (330, "Kube config creation failed")

    config.load_kube_config(config_file=k8s_kubefilepath)
    configuration = client.Configuration()
    configuration.api_key['authorization'] = _get_bearer_token(clustername, aws_region)
    # print("Token: ", configuration.api_key['authorization'] )
    configuration.api_key_prefix['authorization'] = 'Bearer'

    # API
    # Uncomment the following lines to enable debug logging
    # configuration.debug = True
    # apps_v1 = client.AppsV1Api(api_client=client.ApiClient(configuration))
    apps_v1 = client.AppsV1Api()
    deployment = _create_deployment_object(DEPLOYMENT_NAME, model_imagename_ecrpath, replicas)
    # print("deployment: ", deployment)
    try:
        api_response = apps_v1.create_namespaced_deployment(
            body=deployment,
            namespace=k8s_namespace)
    except Exception as e:
        print(str(e))
        # raise e
        return (340, str(e))

    # print("Deployment created. status='%s'" % str(api_response.status))
    # wait till deployment object is launched and all pods meeting scaling policy are available.
    if _wait_for_deployment_complete(DEPLOYMENT_NAME, replicas, k8s_pods_creation_timeout, apps_v1, k8s_namespace):
        print("Deployment complete. Number of pods available:", replicas)

    #########

    #   proceed creating service and ingress objects
    try:
        svc_body = _create_service_body(k8s_service_name, DEPLOYMENT_NAME)
        v1 = client.CoreV1Api()
        v1.create_namespaced_service(namespace=k8s_namespace, body=svc_body)

        ingress_body = _create_ingress_body(k8s_ingress_name, k8s_service_name, unique_env_id)
        networking_v1_beta1_api = client.NetworkingV1beta1Api()
        networking_v1_beta1_api.create_namespaced_ingress(namespace=k8s_namespace, body=ingress_body)

    except Exception as e:
        print(str(e))
        # raise e
        return (350, str(e))

    return (0, "Success")


###

def delete_k8s_deployment(k8s_clustername, k8s_namespace, aws_region, unique_env_id):
    # keep following name in mind as this is the selector and used across the board.
    # Referred again in "service.yaml" and loadbabalncer.yaml object creation

    k8s_deployment_name = "scoringsasmm-" + unique_env_id
    k8s_service_name = "scoringsassvc-" + unique_env_id
    k8s_ingress_name = "scoringsasing-" + unique_env_id
    k8s_kubefilepath = "/tmp/tmp_kubeconfig"

    # we reuse /tmp/kubeconfig since we created it earlier during deployment/pod launch.
    #
    config.load_kube_config(config_file=k8s_kubefilepath)
    configuration = client.Configuration()
    configuration.api_key['authorization'] = _get_bearer_token(k8s_clustername, aws_region)
    configuration.api_key_prefix['authorization'] = 'Bearer'

    # API
    # Uncomment the following lines to enable debug logging
    # configuration.debug = True
    # apps_v1 = client.AppsV1Api(api_client=client.ApiClient(configuration))
    apps_v1 = client.AppsV1Api()
    extensions_v1beta1 = client.ExtensionsV1beta1Api()
    delete_options = client.V1DeleteOptions()
    delete_options.grace_period_seconds = 0
    delete_options.propagation_policy = 'Foreground'
    try:
        api_response = extensions_v1beta1.delete_namespaced_deployment(
            name=k8s_deployment_name,
            body=delete_options,
            grace_period_seconds=0,
            namespace=k8s_namespace)
        print("Deleted deployment:", k8s_deployment_name)
    except Exception as e:
        print(str(e))
        return (910, "deployment not deleted" + k8s_deployment_name)

    # delete services
    v1 = client.CoreV1Api()
    delete_options = client.V1DeleteOptions()
    try:
        api_response = v1.delete_namespaced_service(k8s_service_name, k8s_namespace, body=delete_options)
    except client.rest.ApiException as e:
        print(str(e))
        return (920, "service not deleted" + k8s_service_name)
    print('deleted svc/{} from ns/{}'.format(k8s_service_name, k8s_namespace))

    # delete ingress which is part of extensionsapi
    api_instance = client.ExtensionsV1beta1Api(client.ApiClient(configuration))
    try:
        api_response = api_instance.delete_namespaced_ingress(k8s_ingress_name, k8s_namespace, body=delete_options)
    except client.rest.ApiException as e:
        print(str(e))
        return (930, "ingress not deleted" + k8s_ingress_name)
    print('deleted ingress/{} from ns/{}'.format(k8s_ingress_name, k8s_namespace))

    return (0, "K8S cleanup good")


###### never used. just for testing
if __name__ == '__main__':
    s3folderin = "s3://fsbu-sunall-bucket1/folder1/folder2"
    model_imagename = 'jakochdockermodel'

###### never used. just for testing
if __name__ == '__main__':
    s3folderin = "s3://fsbu-sunall-bucket1/folder1/folder2"
    model_imagename = 'jakochdockermodel'
