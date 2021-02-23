
__version__ = '1.0'

import pkgutil
import boto3

from awsdest.utils.core_aws import *
from awsdest.utils.core_aws_lambda import *


if __name__ == '__main__':

    awsconfig, k8sconfig, siteconfig, lambdaconfig = init_config()
    #print("awsconfig", awsconfig)
    #print("k8sconfig", k8sconfig)
    #print("site-specific: ", siteconfig)
    print("lambda-specific", lambdaconfig)
    print("#################")

    lambda_layer_arn, lambda_layer_version = create_lambda_layer(awsconfig, lambdaconfig)

    print("Lambda Layer ARN: ", lambda_layer_arn)
    print("Lambda Layer Version: ", lambda_layer_version)
    print("Above values needs to go config.properties so they can be picked by setup_lambda_scoring")


