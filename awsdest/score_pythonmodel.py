
__version__ = '1.0'

import pkgutil
import argparse
import boto3

from awsdest.utils.core_aws import *
from awsdest.utils.core_k8s import *
from awsdest.utils.core_restapi_score import *

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='score python models on EKS')
    parser.add_argument('model_imagename', help='Name of Image representing Model on ECR')
    parser.add_argument('s3folderin', help='S3 input bucket and folder path')
    parser.add_argument('s3folderout', help='S3 output bucket and folder path')
    cmd_args = vars(parser.parse_args())
    model_imagename, s3folderin, s3folderout = (cmd_args.get(key) for key in ['model_imagename','s3folderin','s3folderout'])
    # print("Arguments supplied are: ", cmd_args)

    awsconfig, k8sconfig, siteconfig, lambdaconfig = init_config()
    #print("awsconfig", awsconfig)
    #print("k8sconfig", k8sconfig)
    #print("site-specific: ", siteconfig)

        # Validate EKS cluster if it is active, check for image presence in ECR and ofcourse s3 folders
    if validate_exist_scoringimg_eks_files(awsconfig,s3folderin,s3folderout,model_imagename):
        print(" Validated EKS cluster to be Active and Model image is available on ECR.")
    else:
        print("Validation failed. Scoring not started and aborted.")
        exit(1)

    # Take the image from ECR and push it as "deployment app" to K8S cluster. Create LoadBalancer and get the URL
    k8s_cluster_url_for_scoring = create_deployment_for_model(awsconfig, k8sconfig, siteconfig, s3folderin, model_imagename, scaling_policy="simple-total-file-based")
    print("K8S cluster URL for scoring:", k8s_cluster_url_for_scoring)
    #k8s_cluster_url_for_scoring = "http://a14e641746d2d11ea95fd0a133d548d9-809519212.us-east-1.elb.amazonaws.com:8080/"
    #k8s_cluster_url_for_scoring = "http://af544bdb56f7611ea95fd0a133d548d9-1901578039.us-east-1.elb.amazonaws.com"

    # Validate the scoring service by using ping/pong.
    if not validate_pingpong_on_scoring(k8s_cluster_url_for_scoring):
        if awsconfig['ingress_controller_url'] == None:
            #print("no pong received. Let us try updating ELB Load Balancer Ingress with python client network CIDR")
            allow_python_client_CIDR_to_EKSloadBalancer(awsconfig, k8s_cluster_url_for_scoring, siteconfig['python_client_network_cidr'])
        print("Wait for Max 5 min for DNS Name propagation and service availability..")
        start = time.time()
        while (time.time() - start) < 300 and (not validate_pingpong_on_scoring(k8s_cluster_url_for_scoring)):
            time.sleep(10)
        if not validate_pingpong_on_scoring(k8s_cluster_url_for_scoring):
           print(" Still no response from scoring service. Debug it manually. Aborting scoring rpcoess")
           exit(1)
    print("Yes pong received from scoring service checkout. proceeding to scoring step")

    # Score the app
    score_s3files_controller(awsconfig,s3folderin,s3folderout,k8s_cluster_url_for_scoring,siteconfig['time.limit.on.scoring'])

    # clean up k8s_resources - deployment, service and loadbalancer
    delete_k8s_deployment(awsconfig, k8sconfig, siteconfig)



