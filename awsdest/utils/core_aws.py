import configparser
from urllib.parse import urlparse
import boto3
import json
from botocore.exceptions import ClientError

########
##
def init_config():
    config_file_name = "config.properties"
    print("Loading config properties..")
    try:
        config = configparser.RawConfigParser()
        config.read(config_file_name)
        awsconfig = {'aws_access_key_id':config.get('AWS', 'access.key.id'), 'aws_secret_access_key': config.get('AWS', 'secret.access.key'),
                 'aws_region': config.get('AWS','region'),'aws_eks_cluster_name': config.get('AWS','eks.cluster.name'),
                     'ingress_controller_url': config.get('AWS', 'ingress.controller.url', fallback=None)
                 }
        k8sconfig= {'k8s_deployment_name': config.get('K8S', 'k8s.deployment.name'),
                 'k8s_service_name': config.get('K8S', 'k8s.service.name'),
                 'k8s_lbalancer_name': config.get('K8S', 'k8s.lbalancer.name', fallback=None),
                 'k8s_ingress_name': config.get('K8S','k8s.ingress.name', fallback=None ),
                 'k8s_namespace': config.get('K8S', 'k8s.namespace'),
                 'k8s_kubeconfig_path':config.get('K8S','k8s.kubeconfig.path')
                 }
        siteconfig={'python_client_network_cidr':config.get('site-specific','python.client.network.cidr'),
                 'k8s_pods_creation_timeout':int(config.get('site-specific','k8s.pods.creation.timeout')),
                 'time.limit.on.scoring':int(config.get('site-specific','time.limit.on.scoring'))
                 }
        if 'lambda' in config:
            lambdaconfig={
                  'lambda_execution_arn_role' : config.get('lambda', 'lambda.execution.arn.role'),
                'lambda_function_name': config.get('lambda', 'lambda.function.name'),
                'lambda_layer_arn': config.get('lambda','lambda.layer.arn', fallback=None),
                'lambda_layer_version': config.get('lambda', 'lambda.layer.version', fallback=None)
                 }
        else:
            lambdaconfig = None

        return (awsconfig, k8sconfig, siteconfig, lambdaconfig)
    except:
        print("Error loading configuration from config.properties file! ")
        print(traceback.format_exc())
        return 1

########
#
# returns number of files in s3 folder. Excludes parent directory and returns number of files
def number_of_files_s3folder(awsconfig, s3folderin):
    parsed = urlparse(s3folderin, allow_fragments=False)
    bucketin = parsed.netloc
    prefix = parsed.path.lstrip('/')
    try:
        s3_client = boto3.client('s3', aws_access_key_id=awsconfig['aws_access_key_id'], aws_secret_access_key=awsconfig['aws_secret_access_key'])
        kwargs = {'Bucket': bucketin, 'Prefix': prefix}
        response = s3_client.list_objects_v2(**kwargs)
        # print("s3 response for folder", s3folderin, response)
        return (response['KeyCount'] - 1)  # Subtract 1 to remove the parent folder from list
    except ClientError as e:
        print("Unexpected error while trying to check on S3 Buckets: %s" % e)
        return -1


########
####
def validate_exist_scoringimg_eks_files(awsconfig, s3folderin, s3folderout, model_imagename):

    # Validate S3 Input folder
    if number_of_files_s3folder(awsconfig,s3folderin) <= 0:
        print("S3 input folder non existent or empty folder with no files to score")
        return False

    #  validate S3 output folder
    if number_of_files_s3folder(awsconfig, s3folderout) < 0:
        print("S3 output folder non existent.")
        return False

    # check if ECR image is available
    try:
        ecr_client = boto3.client('ecr', aws_access_key_id=awsconfig['aws_access_key_id'],
                             aws_secret_access_key=awsconfig['aws_secret_access_key'], region_name=awsconfig['aws_region'])
        response = ecr_client.describe_images(repositoryName=model_imagename)
        if (response['ResponseMetadata']['HTTPStatusCode'] != 200):
            print("Image not found or something else wrong with repo")
            return False
    except ClientError as e:
        print("Unexpected error while trying to check on Model image presence: %s" % e)
        return False

    # check if EKS cluster is running and active
    try:
        eks_client = boto3.client('eks', aws_access_key_id=awsconfig['aws_access_key_id'],
                             aws_secret_access_key=awsconfig['aws_secret_access_key'], region_name=awsconfig['aws_region'])
        response = eks_client.describe_cluster(name = awsconfig['aws_eks_cluster_name'] )
        if (response['ResponseMetadata']['HTTPStatusCode'] != 200) or (response['cluster']['status'] != 'ACTIVE'):
            print("EKS cluster is not Active:", awsconfig['aws_eks_cluster_name'])
            print(response)
            return False
    except ClientError as e:
        print("Unexpected error while trying to check on EKS cluster: %s" % e)
        return False
    #print("S3 folders exist")
    #print("Validated Model Image available on ECR. Validated EKS Cluster to be Active")
    #print("All environment check out passed. Proceeding to next step")
    return True


def allow_python_client_CIDR_to_EKSloadBalancer(awsconfig, k8s_cluster_url_for_scoring, python_client_network_cidr):
    k8s_elb_sg_groupname = "k8s-elb-" + k8s_cluster_url_for_scoring.lstrip("http://").rsplit("-")[0]  # Bad coding. Replace [0] with list iterate but again it is supposed to give just 1 item - Sudhir Reddy

    try:
        ec2_client = boto3.client('ec2', aws_access_key_id=awsconfig['aws_access_key_id'],
                              aws_secret_access_key=awsconfig['aws_secret_access_key'],
                              region_name=awsconfig['aws_region'])
        http_resp = ec2_client.authorize_security_group_ingress(GroupName=k8s_elb_sg_groupname,
                                                                IpPermissions=[
                                                                  {
                                                                    'IpProtocol' : '-1',
                                                                    'IpRanges': [
                                                                        {
                                                                            'CidrIp' : python_client_network_cidr,
                                                                            'Description' : "Allow Python scoring client to EKS ELB"
                                                                        }
                                                                    ]
                                                                  }
                                                                ]
        )
        #print(http_resp)

    except ClientError as e:
        print("Unexpected error while trying to add a Ingress rule to EC2 security group: %s" % e)
        return False

def generate_presigned_url_for_list(awsconfig, s3folder):
    parsed = urlparse(s3folder, allow_fragments=False)
    bucketin = parsed.netloc
    prefix = parsed.path.lstrip('/')
    try:
        s3_client = boto3.client('s3', aws_access_key_id=awsconfig['aws_access_key_id'],
                                 aws_secret_access_key=awsconfig['aws_secret_access_key'])
        kwargs = {'Bucket': bucketin, 'Prefix': prefix}
        return s3_client.generate_presigned_url(ClientMethod='list_objects_v2',Params=kwargs)
    except ClientError as e:
        print("Unexpected error while generating presigned url for s3 : %s" % e)
        return -1

def generate_presigned_url_for_getobject(awsconfig, client_method_name,s3folder, objectkey):
    parsed = urlparse(s3folder, allow_fragments=False)
    bucketin = parsed.netloc
    prefix = parsed.path.lstrip('/')
    try:
        s3_client = boto3.client('s3', aws_access_key_id=awsconfig['aws_access_key_id'],
                                 aws_secret_access_key=awsconfig['aws_secret_access_key'])
        kwargs = {'Bucket': bucketin, 'Key': objectkey}
        return s3_client.generate_presigned_url(ClientMethod=client_method_name, Params=kwargs)
    except ClientError as e:
        print("Unexpected error while generating presigned url for s3 get: %s" % e)
        return -1


def generate_presigned_url_for_postobject(awsconfig,s3folder,objectkey):
    parsed = urlparse(s3folder, allow_fragments=False)
    bucketin = parsed.netloc
    prefix = parsed.path.lstrip('/')
    try:
        s3_client = boto3.client('s3', aws_access_key_id=awsconfig['aws_access_key_id'],
                                 aws_secret_access_key=awsconfig['aws_secret_access_key'])
        return s3_client.generate_presigned_post(bucketin,objectkey)
    except ClientError as e:
        print("Unexpected error while generating presigned url for s3 post object: %s" % e)
        return -1
###############
