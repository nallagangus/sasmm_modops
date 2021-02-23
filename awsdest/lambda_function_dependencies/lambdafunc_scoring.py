import boto3
import os
import sys
import uuid
from urllib.parse import unquote_plus

from utils.lambdafunc_core_aws import *
from utils.lambdafunc_core_k8s import *
from utils.lambdafunc_core_restapi_score import *

#
import urllib.request
import socket


##

def scoring_lambda_handler(event, context):
    aws_region = os.environ['aws_region']

    external_ip = urllib.request.urlopen('https://ident.me').read().decode('utf8')
    print("External IP to add to Firewall ingress rules:", external_ip)
    print("======= ")

    fallback_modelname = os.environ['fallback_modelname']
    fallback_clustername = os.environ['fallback_clustername']
    fallback_ingress_url = os.environ['fallback_ingress_url']
    fallback_k8s_namespace = os.environ['k8s_namespace']

    s3out_folder = os.environ['s3_outputfolder']
    s3out_bucket = urlparse(s3out_folder, allow_fragments=False).netloc

    s3out_parsed = urlparse(s3out_folder, allow_fragments=False)
    s3out_prefix = s3out_parsed.path.lstrip('/')

    # reference structure of S3 event message at https://docs.aws.amazon.com/AmazonS3/latest/dev/notification-content-structure.html
    for record in event['Records']:
        scoring_start_time = time.ctime()
        s3in_bucket = record['s3']['bucket']['name']
        s3in_objectkey = unquote_plus(record['s3']['object']['key'])
        s3out_objectkey = s3out_prefix + path.basename(s3in_objectkey) + ".scoreout"

        # process tags to decide on what model, cluster, ingress and namespace we should act on.
        process_tags_code, process_text, modelimagename, clustername, ingress_scoring_url, k8s_namespace = \
            process_tags_on_file(s3in_bucket, s3in_objectkey, fallback_modelname, fallback_clustername,
                                 fallback_ingress_url, fallback_k8s_namespace)
        if not process_tags_code == 0:
            err_msg = "Scoring Terminated for this file" + process_text
            s3outfile_presignedurl = get_s3presignedurl_for_post(s3out_bucket, s3out_objectkey + ".error")
            upload_file_s3(s3outfile_presignedurl, text_message=err_msg)
            return 100

        # Validate model and EKS supplied.
        validate_model_eks, validate_message = validate_modelimage_eks(modelimagename, clustername, aws_region)
        if not validate_model_eks:
            err_msg = validate_message + "Scoring terminated for this file. Either ModelImage or clustername is invalid. Please delete the original file in input bucket folder and recreate file with correct tags for ModelName. Just correcting tags will not invoke scoring."
            s3outfile_presignedurl = get_s3presignedurl_for_post(s3out_bucket, s3out_objectkey + ".error")
            upload_file_s3(s3outfile_presignedurl, text_message=err_msg)
            return 200

        # deploy and check if that is good.
        # We use unique_file_identifier to have a dedicated pod for each file received as a S3 Event. deployment_name cannot have / or ..
        unique_env_id = s3in_objectkey.replace("/", "-").replace(".", "-")
        ingress_scoring_url = ingress_scoring_url + "/" + unique_env_id
        print("Ingress scoring url:", ingress_scoring_url)
        print("Env this instance of lambda func dealing :", unique_env_id)

        deployment_status_code, text_message = create_deployment_for_model(modelimagename, clustername, k8s_namespace,
                                                                           aws_region, unique_env_id)
        if not deployment_status_code == 0:
            err_msg = text_message + "Deployment of POD failed. Scoring Aborted. Debug manually "
            s3outfile_presignedurl = get_s3presignedurl_for_post(s3out_bucket, s3out_objectkey + ".error")
            upload_file_s3(s3outfile_presignedurl, text_message=err_msg)
            return deployment_status_code

        # wait for 2 min for ingress rule to commence handling traffic
        start = time.time()
        while (time.time() - start) < 120 and (not validate_pingpong_on_scoring(ingress_scoring_url)):
            time.sleep(10)
        if not validate_pingpong_on_scoring(ingress_scoring_url):
            err_msg = 'Scoring terminated for this file.Ingress not responding to ping. Please check K8S deployment and Ingress. We did not delete the K8S deployment and Ingress for debugging. Please delete them after debug.'
            err_msg_2 = "Also add External IP Address " + external_ip + " to Load Balancer Inbound Firewall rules. Lambda does not run in VPC by default and so this address may change from time to time."
            s3outfile_presignedurl = get_s3presignedurl_for_post(s3out_bucket, s3out_objectkey + ".error")
            upload_file_s3(s3outfile_presignedurl, text_message=err_msg + err_msg_2)
            return 400

        s3infile_presignedurl = get_s3presignedurl(s3in_bucket, s3in_objectkey)
        s3outfile_presignedurl = get_s3presignedurl_for_post(s3out_bucket, s3out_objectkey)
        scoring_status_code, scoring_msg = score_file_process(s3infile_presignedurl, s3outfile_presignedurl,
                                                              ingress_scoring_url, unique_env_id)
        if not scoring_status_code == 0:
            err_msg = 'Scoring errors. Did not delete EKS pods. Please delete them after debug.' + scoring_msg
            s3outfile_presignedurl = get_s3presignedurl_for_post(s3out_bucket, s3out_objectkey + ".error")
            upload_file_s3(s3outfile_presignedurl, text_message=err_msg)
            return scoring_status_code

        scoring_end_time = time.ctime()
        s3_taglist = [
            {'Key': 'Source of file scored', 'Value': s3in_bucket + "/" + s3in_objectkey},
            {'Key': 'model_used_for_scoring', 'Value': modelimagename},
            {'Key': 'Duration', 'Value': scoring_start_time + scoring_end_time},
            {'Key': 'eks_used_for_scoring:namespace', 'Value': clustername + ":" + k8s_namespace},
        ]
        update_tags_s3file(s3out_bucket, s3out_objectkey, s3_taglist)

        delete_status_code, text_message = delete_k8s_deployment(clustername, k8s_namespace, aws_region, unique_env_id)
        if not delete_status_code == 0:
            err_msg = text_message + "Scoring may have completed. But K8S assets not cleanuped. Do it manually"
            s3outfile_presignedurl = get_s3presignedurl_for_post(s3out_bucket, s3out_objectkey + ".error")
            upload_file_s3(s3outfile_presignedurl, text_message=err_msg)
            return delete_status_code

        return 0

