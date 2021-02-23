from urllib.parse import urlparse
import boto3
from botocore.exceptions import ClientError

from awsdest.utils.core_aws import *

def _isthere__s3_bucketfolder_notification_config(awsconfig, s3folder):
    parsed = urlparse(s3folder, allow_fragments=False)
    bucketin = parsed.netloc
    s3folder_prefix = parsed.path.lstrip('/')
    try:
        s3_client = boto3.client('s3', aws_access_key_id=awsconfig['aws_access_key_id'],
                                 aws_secret_access_key=awsconfig['aws_secret_access_key'])
        response = s3_client.get_bucket_notification_configuration(Bucket=bucketin)

        if 'LambdaFunctionConfigurations' in response or \
                'TopicConfigurations' in response or \
                'QueueConfigurations' in response :
            # print("This bucket does event config set"
            return True

        return False

        # following will never be executed by design. Since we want to aboid creating event notifications if there is some on bucket already.
        # our process wipes out previos configurations.
        lambdaFuncConfigs_list = response['LambdaFunctionConfigurations']
        for lc in lambdaFuncConfigs_list:
            lc1 = lc['Filter']['Key']['FilterRules']
            for lc2 in lc1:
                if lc2['Name'] == "Prefix" and str.rstrip(lc2['Value'], "/") == str.rstrip(s3folder_prefix,"/"):  # if folder prefix matches then there is a notification on that folder prefix
                    print("This s3bucket:/folder has lambda functon configuration already:", lc)
                    return True

    except ClientError as e:
        print("Error getting s3_event_configuration " % e)
        return Null


def validate_lambda_folders(awsconfig, s3folderin_lambda, s3folderout_lambda):
    if number_of_files_s3folder(awsconfig, s3folderin_lambda) < 0:
        print("S3 input folder non existent.")
        return False

    if number_of_files_s3folder(awsconfig, s3folderout_lambda) < 0:
        print("S3 output folder non existent.")
        return False

    # check if input and output folder as same. We do not want events/triggers that result in an infinite loop of executions
    if str.rstrip(s3folderin_lambda, "/") == str.rstrip(s3folderout_lambda, "/"):
        print(" cannot have input and output folders same to prevent infinite lambda loop triggers")
        return False

    # check if there are events/lambda defined on this bucket and folder specifically
    # we do not want conflicts. So being extra careful.
    if _isthere__s3_bucketfolder_notification_config(awsconfig, s3folderin_lambda):
        print("Event configuration exists on this bucket. Do not want to create additional configuration:",
              s3folderin_lambda)
        return False

    if _isthere__s3_bucketfolder_notification_config(awsconfig, s3folderout_lambda):
        print("Event configuration exists on this folder. Do not want to create additional configuration:",
              s3folderout_lambda)
        return False

    return True


def create_lambda_layer(awsconfig, lambdaconfig):
    l = open('lambda_layers/lambda_layers_k8s_requests_etc_packages_1.zip', 'rb')
    layer_zipfile = l.read()

    try:
        lambda_client = boto3.client('lambda', aws_access_key_id=awsconfig['aws_access_key_id'],
                                     aws_secret_access_key=awsconfig['aws_secret_access_key'])
        response = lambda_client.publish_layer_version(LayerName='lambda_scoring_layer',
                        Content={'ZipFile': layer_zipfile
                        },
        )
        lambda_layer_arn  = response['LayerArn']
        lambda_layer_version = response['Version']

        #layer_arn_ver = 'arn:aws:lambda:us-east-1:617292774228:layer:lambda_scoring_layer:1'
        #response = lambda_client.add_layer_version_permission(
        #    LayerName='lambda_scoring_layer',
        #    VersionNumber='$LATEST',
        #    StatementId='1',
        #    Action='lambda:GetLayerVersion',
        #    Principal='*'
        #)

        return (lambda_layer_arn, lambda_layer_version)

    except ClientError as e:
        print("Error creating lambda layer " % e)
        return Null


def _create_lambda_function(awsconfig, lambdaconfig, k8sconfig, model_imagename, s3folderin_lambda, s3folderout_lambda):
    aws_eks_cluster_name = awsconfig['aws_eks_cluster_name']
    ingress_controller_url = awsconfig['ingress_controller_url']
    aws_region = awsconfig['aws_region']
    k8s_namespace = k8sconfig['k8s_namespace']

    lambda_execution_role = lambdaconfig['lambda_execution_arn_role']
    lambda_function_name = lambdaconfig['lambda_function_name']
    lambda_layer_arn = lambdaconfig['lambda_layer_arn']
    lambda_layer_version = lambdaconfig['lambda_layer_version']

    f = open('lambda_function_dependencies/lambdafunc_scoring_dependencies.zip', 'rb')
    function_zipfile = f.read()

    try:
        lambda_client = boto3.client('lambda', aws_access_key_id=awsconfig['aws_access_key_id'],
                                     aws_secret_access_key=awsconfig['aws_secret_access_key'])

        lambda_layer_arn_version = lambda_layer_arn + ":" + lambda_layer_version

        response = lambda_client.create_function(
            FunctionName=lambda_function_name,
            Runtime='python3.7',
            Role=lambda_execution_role,
            Handler='lambdafunc_scoring.scoring_lambda_handler',
            Code={
                'ZipFile': function_zipfile,
            },
            Timeout=899,
            MemorySize=128,
            Environment={
                'Variables': {
                    'fallback_modelname': model_imagename,
                    'fallback_clustername': aws_eks_cluster_name,
                    'fallback_ingress_url' : ingress_controller_url,
                    's3_outputfolder': s3folderout_lambda,
                    'aws_region': aws_region,
                    'k8s_namespace' : k8s_namespace
                }
            },
            Layers=[lambda_layer_arn_version]
        )
        functionArn = response['FunctionArn']

        response = lambda_client.add_permission(
            FunctionName=functionArn,
            StatementId='1',
            Action='lambda:InvokeFunction',
            Principal='s3.amazonaws.com'
        )
        return functionArn

    except ClientError as e:
        print("Error creating lambda function " % e)
        return Null


def _create_s3bucket_notificationfunc(awsconfig, lambdaconfig, s3folderin_lambda, functionArn):
    parsed = urlparse(s3folderin_lambda, allow_fragments=False)
    bucketin = parsed.netloc
    s3prefix = parsed.path.lstrip('/')

    lambda_function_name = lambdaconfig['lambda_function_name']

    try:
        s3_client = boto3.client('s3', aws_access_key_id=awsconfig['aws_access_key_id'],
                                 aws_secret_access_key=awsconfig['aws_secret_access_key'])
        response = s3_client.put_bucket_notification_configuration(
            Bucket=bucketin,
            NotificationConfiguration={
                'LambdaFunctionConfigurations': [
                    {
                        'Id': lambda_function_name,
                        'LambdaFunctionArn': functionArn,
                        'Events': [
                            's3:ObjectCreated:*'
                        ],
                        'Filter': {
                            'Key': {
                                'FilterRules': [
                                    {
                                        'Name': 'prefix', 'Value': s3prefix
                                    },
                                    {
                                        'Name': 'suffix', 'Value': '.csv'
                                    }
                                ]
                            }
                        }
                    }
                ]
            }
        )
        print("Event Notification created on S3 Input bucket that invokes above Lambda Function", bucketin)
    except ClientError as e:
        print("Error creating event notification configuration on s3 bucket " % e)
        return Null
    # except Exception as e:
    #    print(str(e))
    #    raise e


def create_lambda_setup(awsconfig,lambdaconfig, k8sconfig, model_imagename, s3folderin_lambda, s3folderout_lambda):
    functionArn = _create_lambda_function(awsconfig, lambdaconfig, k8sconfig, model_imagename, s3folderin_lambda, s3folderout_lambda)
    print("Lambda Function created functionArn:", functionArn)
    _create_s3bucket_notificationfunc(awsconfig, lambdaconfig, s3folderin_lambda, functionArn)


######

def _delete_s3bucket_notificationfunc(awsconfig, s3folderin_lambda):
    parsed = urlparse(s3folderin_lambda, allow_fragments=False)
    bucketin = parsed.netloc
    try:
        # delete by saving an empty notification configuration.
        s3_client = boto3.client('s3', aws_access_key_id=awsconfig['aws_access_key_id'],
                                 aws_secret_access_key=awsconfig['aws_secret_access_key'])
        response = s3_client.put_bucket_notification_configuration(
            Bucket=bucketin,
            NotificationConfiguration={
            }
        )
        print("Deleted ALL Event notification configurations on bucket ", )
    except ClientError as e:
        print("Error creating event notification configuration on s3 bucket " % e)
        return Null
    # except Exception as e:
    #    print(str(e))
    #    raise e


def _delete_lambda_function(awsconfig, lambdaconfig):
    lambda_function_name = lambdaconfig['lambda_function_name']

    try:
        lambda_client = boto3.client('lambda', aws_access_key_id=awsconfig['aws_access_key_id'],
                                     aws_secret_access_key=awsconfig['aws_secret_access_key'])
        response = lambda_client.delete_function(FunctionName=lambda_function_name)

        # do not delete layer here. see why @ https://stackoverflow.com/questions/60824745/aws-delete-lambda-layer-still-retains-layer-version-history/61103244#61103244

        return True

    except ClientError as e:
        print("Error creating lambda function or layer " % e)
        return Null


def delete_lambda_setup(awsconfig, lambdaconfig, s3folderin_lambda):
    _delete_s3bucket_notificationfunc(awsconfig,s3folderin_lambda)  # deletes all notifcations. Bad. Restricting to folder prefix is not provided by AWS SDK.
    _delete_lambda_function(awsconfig, lambdaconfig)
