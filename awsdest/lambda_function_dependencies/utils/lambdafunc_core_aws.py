import configparser
from urllib.parse import urlparse
import boto3
import json
from botocore.exceptions import ClientError


########

def validate_modelimage_eks(model_imagename, clustername, awsregion):
    # check if ECR image is available
    try:
        #print("check on model:", model_imagename)
        ecr_client = boto3.client('ecr', region_name=awsregion)
        response = ecr_client.describe_images(repositoryName=model_imagename)
        if (response['ResponseMetadata']['HTTPStatusCode'] != 200):
            print("Image not found or something else wrong with repo")
            return (False, "ModelImage not found" + model_imagename)
    except ClientError as e:
        print("Unexpected error while trying to check on Model image presence: %s" % e)
        return (False, str(e))

    # check if EKS cluster is running and active
    try:
        #print("check on eks:", clustername)
        eks_client = boto3.client('eks', region_name=awsregion)
        response = eks_client.describe_cluster(name=clustername)
        if (response['ResponseMetadata']['HTTPStatusCode'] != 200) or (response['cluster']['status'] != 'ACTIVE'):
            print("EKS cluster is not Active:", clustername)
            print(response)
            return (False, "Cluster not Active:" + clustername)
    except ClientError as e:
        print("Unexpected error while trying to check on EKS cluster: %s" % e)
        return (False, str(e))

    # print("S3 folders exist")
    # print("Validated Model Image available on ECR. Validated EKS Cluster to be Active")
    # print("All environment check out passed. Proceeding to next step")

    return (True, "Model and Cluster are validated.")


##
def _get_s3tags(bucket, objectkey):
    try:
        s3_client = boto3.client('s3')
        kwargs = {'Bucket': bucket, 'Key': objectkey}
        s3_taglist = s3_client.get_object_tagging(**kwargs)['TagSet']
        s3_tagdict = {}
        if not s3_taglist is None:
            for tag in s3_taglist:
                s3_tagdict[tag['Key']] = tag['Value']

        return s3_tagdict

    except ClientError as e:
        print("Unexpected error while getting tags for s3 in file: %s" % e)
        return None

###
def process_tags_on_file(s3in_bucket, s3in_objectkey, fallback_modelname, fallback_clustername, fallback_ingress_url,
                         fallback_k8s_namespace):
    s3_tagdict = _get_s3tags(s3in_bucket, s3in_objectkey)
    if len(s3_tagdict) == 0:
        modelimagename = fallback_modelname
        clustername = fallback_clustername
        ingress_scoring_url = fallback_ingress_url
        k8s_namespace = fallback_k8s_namespace
    else:
        # inspect for all 4 tags we need. Else abort
        if len(s3_tagdict) == 4:
            modelimagename = s3_tagdict.get('modelimagename', '')
            clustername = s3_tagdict.get('k8scluster', '')
            ingress_scoring_url = s3_tagdict.get('k8singressurl', '')
            k8s_namespace = s3_tagdict.get('k8snamespace', '')
        else:
            err_msg = "Aborted scoring. One of the scoring input tag is missing. We need all 4 tags - modelimagename, k8scluster, k8singressurl and k8snamespace. Or leave them empty and we use default values picked from config.properties of application"
            return (100, err_msg, '', '', '', '')

    if modelimagename == '' or clustername == '' or ingress_scoring_url == '' or k8s_namespace == '':
        err_msg = "Aborted scoring as one of tags is missing or misspelled. We need all 4 tags - modelimagename, k8scluster, k8singressurl and k8snamespace. Or leave them empty and we use default values picked from config.properties of application"
        return (100, err_msg, '', '', '', '')

    return (0, "Success", modelimagename, clustername, ingress_scoring_url, k8s_namespace)

###

def update_tags_s3file(s3_bucket, objectkey, s3_taglist):
    try:
        s3_client = boto3.client('s3')
        response = s3_client.put_object_tagging(Bucket=s3_bucket,Key=objectkey,
                        Tagging={
                            'TagSet': s3_taglist
                        }
        )
        return 0
    except ClientError as e:
        print("Unexpected error while updating tags %s" % e)
        return None

###

def get_s3presignedurl(bucket, objectkey):
    try:
        s3_client = boto3.client('s3')
        kwargs = {'Bucket': bucket, 'Key': objectkey}
        s3_presigned_url = s3_client.generate_presigned_url(ClientMethod="get_object", Params=kwargs)
        return s3_presigned_url

    except ClientError as e:
        print("Unexpected error while generating presigned url : %s" % e)
        return None

###
def get_s3presignedurl_for_post(bucket, objectkey):
    try:
        s3_client = boto3.client('s3')
        kwargs = {'Bucket': bucket, 'Key': objectkey, }
        s3_presigned_url = s3_client.generate_presigned_post(bucket, objectkey)
        return s3_presigned_url

    except ClientError as e:
        print("Unexpected error while generating presigned url for posting file: %s" % e)
        return None

