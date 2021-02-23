
__version__ = '1.0'

import pkgutil
import argparse
import boto3

from awsdest.utils.core_aws import *
from awsdest.utils.core_aws_lambda import *
from awsdest.utils.core_k8s import *
from awsdest.utils.core_restapi_score import *

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Lambda scoring setup on S3 folders')
    parser.add_argument('action_lambda_setup', help='Create or Delete lambda setup')
    parser.add_argument('fallback_model_imagename', help='Name of Image representing default Model on ECR')
    parser.add_argument('s3folderin_lambda_scoring', help='S3 input bucket and folder path being set for lambda scoring')
    parser.add_argument('s3folderout_lambda_scoring', help='S3 output bucket and folder path being set for receiving output from scoring')
    cmd_args = vars(parser.parse_args())
    action_lambda_setup, model_imagename, s3folderin_lambda, s3folderout_lambda  = (cmd_args.get(key) for key in
                                                                                    ['action_lambda_setup', 'fallback_model_imagename','s3folderin_lambda_scoring','s3folderout_lambda_scoring'])
    print("Arguments supplied are: ", cmd_args)

    awsconfig, k8sconfig, siteconfig, lambdaconfig = init_config()
    #print("awsconfig", awsconfig)
    #print("k8sconfig", k8sconfig)
    #print("site-specific: ", siteconfig)
    print("lambda-specific", lambdaconfig)
    print("#################")

    if action_lambda_setup == "create":
        # Validate if S3 folders exist and if there is an existing lambdafuncconfig set on those folders.
        if validate_lambda_folders(awsconfig, s3folderin_lambda, s3folderout_lambda):
            print(" Validated S3 Folders for which we are trying to set lambda event processing.")
        else:
            print("Validation failed. May be folders are missing or they already have LambdaFuncConfig defined on them. To Avoid conflicts Lambda scoring HAS NOT been setup.")
            print("This create options does wipe all old event notification configurations including SNS, SQS and Lambda on entire bucket. Please do note that. ")
            exit(1)

        create_lambda_setup(awsconfig, lambdaconfig, k8sconfig, model_imagename, s3folderin_lambda, s3folderout_lambda)

    if action_lambda_setup == "delete":
        delete_lambda_setup(awsconfig, lambdaconfig, s3folderin_lambda)


