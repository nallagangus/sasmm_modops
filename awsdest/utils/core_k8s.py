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
from awsdest.utils.core_aws import *
import time

############

def _get_bearer_token(awsconfig):
    #print("awsconfig in get bearer:", awsconfig)
    STS_TOKEN_EXPIRES_IN = 60
    cluster_id = awsconfig['aws_eks_cluster_name']
    region = awsconfig['aws_region']

    session = boto3.session.Session( aws_access_key_id=awsconfig['aws_access_key_id'],
                             aws_secret_access_key=awsconfig['aws_secret_access_key'], region_name=region)
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

    #print("signed URL:", signed_url)
    base64_url = base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8')
    # remove any base64 encoding padding:
    return 'k8s-aws-v1.' + re.sub(r'=*', '', base64_url)

############

def _create_kube_config(awsconfig, KUBE_FILEPATH):

    try:
        eks_client = boto3.client('eks', aws_access_key_id=awsconfig['aws_access_key_id'],
                             aws_secret_access_key=awsconfig['aws_secret_access_key'], region_name=awsconfig['aws_region'])
        response = eks_client.describe_cluster(name = awsconfig['aws_eks_cluster_name'] )
    except ClientError as e:
        print("Unexpected error while describing cluster : %s" % e)
        return 1

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

def _wait_for_deployment_complete(deployment_name,replicas, timeout, k8s_api_v1_client,k8s_namespace):
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

def _get_loadbalancerURL(service_api_instance, servicename,k8s_namespace):
     try:
        response = service_api_instance.read_namespaced_service(name=servicename, namespace=k8s_namespace)
        #print("Loadbalancer URL availabiility response: ", response)
        lb_hostname = response.status.load_balancer.ingress[0].hostname
        if lb_hostname is not NameError and lb_hostname is not None:
            #print("lb hostanme:", lb_hostname)
            return response.status.load_balancer.ingress[0].hostname
     except:
            return None


def create_deployment_for_model(awsconfig, k8sconfig, siteconfig, s3folderin, model_imagename, scaling_policy):
    # keep following name in mind as this is the selector and used across the board.
    # Referred again in "service.yaml" and loadbabalncer.yaml object creation
    DEPLOYMENT_NAME = k8sconfig['k8s_deployment_name']
    k8s_service_name = k8sconfig['k8s_service_name']
    k8s_lbalancer_name = k8sconfig['k8s_lbalancer_name']
    k8s_namespace = k8sconfig['k8s_namespace']
    k8s_kubefilepath = k8sconfig['k8s_kubeconfig_path']
    k8s_pods_creation_timeout = siteconfig['k8s_pods_creation_timeout']
    ingress_controller_url = awsconfig['ingress_controller_url']

    try:
        ecr_client = boto3.client('ecr', aws_access_key_id=awsconfig['aws_access_key_id'],
                                  aws_secret_access_key=awsconfig['aws_secret_access_key'],
                                  region_name=awsconfig['aws_region'])
        response = ecr_client.describe_images(repositoryName=model_imagename)
        model_imagename_ecrpath = response['imageDetails'][0]['registryId'] + ".dkr.ecr." + awsconfig['aws_region'] + ".amazonaws.com/" + model_imagename + ":latest"
        if (response['ResponseMetadata']['HTTPStatusCode'] != 200):
            print("Image not found or something else wrong with repo")
            exit
    except ClientError as e:
        print("Unexpected error while trying to check on Model image presence: %s" % e)
        exit

    if scaling_policy == "simple-total-file-based":
       replicas = number_of_files_s3folder(awsconfig, s3folderin)
    else:
        replicas = 1

    if not _create_kube_config(awsconfig, k8s_kubefilepath):
        print("Kube config creation failed:")
        return 1

    config.load_kube_config(config_file=k8s_kubefilepath)
    configuration = client.Configuration()
    configuration.api_key['authorization'] = _get_bearer_token(awsconfig)
    configuration.api_key_prefix['authorization'] = 'Bearer'

    # API
    # Uncomment the following lines to enable debug logging
    #configuration.debug = True
    #apps_v1 = client.AppsV1Api(api_client=client.ApiClient(configuration))
    apps_v1 = client.AppsV1Api()

    deployment = _create_deployment_object(DEPLOYMENT_NAME, model_imagename_ecrpath,replicas)
    #print("deployment: ", deployment)
    try:
        api_response = apps_v1.create_namespaced_deployment(
            body=deployment,
            namespace=k8s_namespace)
    except Exception as e:
        print(str(e))
        raise e

    #print("Deployment created. status='%s'" % str(api_response.status))
    # wait till deployment object is launched and all pods meeting scaling policy are available.
    if _wait_for_deployment_complete(DEPLOYMENT_NAME, replicas, k8s_pods_creation_timeout, apps_v1, k8s_namespace):
        print("Deployment complete. Number of pods available:", replicas)

    #########

    #   proceed creating service and load balancer objects
    k8s_client = client.api_client.ApiClient(configuration=configuration)
    try:
        # create service object.
        utils.create_from_yaml(k8s_client=k8s_client, yaml_file="k8s_pythonscoringmodel_service.yaml",verbose=True, namespace=k8s_namespace)

        # create K8S load balancers and they follow TAG rules as in https://aws.amazon.com/premiumsupport/knowledge-center/eks-load-balancers-troubleshooting/
        # LoadBalancer unlike ClusterIP creates a AWS classic Load balancer under EC2/LB section. Ensure Security group it belongs to can take in requests from clients.
        # Also note the differnce between LB creating in ingress setup vs non-ingress setup. Go back under comments on main config.properties for the differences.
        if ingress_controller_url == None:
            utils.create_from_yaml(k8s_client=k8s_client, yaml_file="k8s_pythonscoringmodel_loadbalancer.yaml", verbose=True, namespace=k8s_namespace)
        else:
            utils.create_from_yaml(k8s_client=k8s_client, yaml_file="k8s_pythonscoringmodel_ingress.yaml", verbose=True, namespace=k8s_namespace)
    except Exception as e:
        print(str(e))
        raise e

    service_api_instance = client.CoreV1Api()
    if ingress_controller_url == None:
        lb_hostname = None
        start = time.time()
        while (time.time() - start) < 300 and (lb_hostname == None):
            lb_hostname = _get_loadbalancerURL(service_api_instance, k8s_lbalancer_name, k8s_namespace)
            time.sleep(10)
        if lb_hostname is not None:
            return "http://" + lb_hostname + ":8080/"
        else:
            print("Load Balancer URL not retreived. Aborting...")
    else:
        return ingress_controller_url


def delete_k8s_deployment(awsconfig, k8sconfig,siteconfig):

    k8s_deployment_name = k8sconfig['k8s_deployment_name']
    k8s_service_name = k8sconfig['k8s_service_name']
    k8s_lbalancer_name = k8sconfig['k8s_lbalancer_name']
    k8s_ingress_name = k8sconfig['k8s_ingress_name']
    k8s_namespace = k8sconfig['k8s_namespace']
    k8s_kubefilepath = k8sconfig['k8s_kubeconfig_path']
    ingress_controller_url = awsconfig['ingress_controller_url']

    # we reuse /tmp/kubeconfig file if it exists.
    # We do not follow this approach during create deployment by design since we may indavertantly use a incorrect one from earlier use
    if not path.exists(k8s_kubefilepath):
        if not _create_kube_config(awsconfig, k8s_kubefilepath):
            print("Kube config creation failed:")
            return 1

    config.load_kube_config(config_file=k8s_kubefilepath)
    configuration = client.Configuration()
    configuration.api_key['authorization'] = _get_bearer_token(awsconfig)
    configuration.api_key_prefix['authorization'] = 'Bearer'

    # API
    # Uncomment the following lines to enable debug logging
    #configuration.debug = True
    #apps_v1 = client.AppsV1Api(api_client=client.ApiClient(configuration))
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
            raise e
            return False

    v1 = client.CoreV1Api()
    delete_options = client.V1DeleteOptions()

    # delete services
    for k8s_service in k8s_service_name, k8s_lbalancer_name:
        try:
            api_response = v1.delete_namespaced_service(k8s_service, k8s_namespace, body=delete_options)
        except client.rest.ApiException as e:
            #print(str(e))
            print("Ignore if we tried to delete a non-existing resource. Only one of Load Balanacer or Ingresses will be deleted. Not both as you can imagine")
        print('deleted svc/{} from ns/{}'.format(k8s_service, k8s_namespace))

    # delete ingress
    if not ingress_controller_url == None:
        api_instance = client.ExtensionsV1beta1Api(client.ApiClient(configuration))
        try:
            api_response = api_instance.delete_namespaced_ingress(k8s_ingress_name, k8s_namespace, body=delete_options)
        except client.rest.ApiException as e:
            print(str(e))
            return False
        print('deleted ingress/{} from ns/{}'.format(k8s_ingress_name, k8s_namespace))

if __name__ == '__main__':
    awsconfig= {'aws_access_key_id': '', 'aws_secret_access_key': '',
     'aws_region': 'us-east-1', 'aws_eks_cluster_name': 'fsbu-sunall-eks-east-1'}
    s3folderin = "s3://fsbu-sunall-bucket1/folder1/folder2"
    model_imagename = 'jakochdockermodel'
    print("K8S cluster URL for scoring:", create_deployment_for_model(awsconfig, s3folderin, model_imagename, scaling_policy="simple-total-file-based"))
