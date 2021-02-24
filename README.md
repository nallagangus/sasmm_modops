# Serverless AWS Lambda model scoring using containerized models and EKS

This project contains prototype implementation of scoring data/files in S3 using pre-trained models available as containers. 

This README describes the usecase background, architecture, pre-requisites for model container images/EKS and a user guide. It also discusses on how to extend for customer specific model container images. An Appendix refers to decisions made during implementation. 

## Usecase

As noted by a cloud provider (LINK) 90% of infrastructure costs come from scoring with only 10% from model development. So, it is important to keep “scoring” costs down and one way is taking on a serverless AWS Lambda approach with ondemand dynamic pods on AWS EKS.

High level usecase requirements:
* Event-driven dynamic scoring - A user dropping a file to a designated input folder on S3 should trigger scoring process.
* Generic solution - Each file can have seperate criteria for scoring - model(container image) based on "S3 tags" attached to the file.
* Serverless - AWS lambda and launch pods as required for scoring and delete pods after the scoring.
* Scalable and distributed - Each file to get a dedicated container there by scaling to any number of files .


## Architecture and Design

* *Event-Driven Dynamic scoring:*
  * Leverage S3 Event notification with “put object” triggering Lambda function. Apply object prefix/suffix conditions to specific “folder” and “.csv” files
* *Generic scoring solution for several “models”:*
  * Each input S3 file to be associated with object tags to indicate “model_image” and “AWS EKS cluster name” for scoring.
  * If “key scoring tags” are missing use a fall back model_image and EKS cluster.
  * If “model image” or EKS cluster are invalid a corresponding “filename.error” to be saved to S3 folder for further actions/correction by User.
  * If scoring fails for any reason “filename.error” to be logged with details for user/data modeler. If those details are not suffice, AWS administrator can look at AWS CloudWatch logs for further steps.
  * If scoring is successful “filename.scoreout” to be saved to S3 output folder. Each scored output file to be tagged with key details such as “model image”, “EKS cluster/namespace” and “Total duration of scoring”.
* *Serverless:*
  * Leverage Lambda as REST API client receiving event notifications from S3. This function when triggered to manage launching K8S “scoring container” and scoring file and saving output back to a S3 folder.
  * Delete pods once scoring is done to extend serverless approach. 
* *Scalable and Distributed*
  * Each file to get a dedicated container. Scaling is done at file level with each file getting a designated container for scoring.

<p align="center">
<img src="https://github.com/nallagangus/sasmm_modops/blob/main/AWS-Scoring-Lambda-Approach.jpg" align="right" width="500" height="500">
</p>

### Workflow
1. User drops a file into S3 bucket configured for event notifications
2. S3 Event notification triggers Lambda function for scoring
3. Our Custom developed Lambda Function (Assumes role lambdaexecutionrole) connects to Kubernetes API server to launch containers
4. K8S server connects with ECR to download SAS Proprietary Python/R Model image (OCI format) from ECR and launches “pod”. Each POD is unique to “file being scored”
5. Lambda function downloads S3 file locally
6. Makes a REST API call to Ingress Load Balancer which routes request to it’s specific POD
7. POD which hosts “model” scores against the file and sends “scored output back” to Lambda
8. Lambda gets file from POD and stores in S3 output bucket.




## Setup and Usage

### Pre-requisites for Model container images

* Pretrained model images(docker container images) should be available on ECR.
* In most cases developers create an application with Python FLASK that will load the model weights and then run a REST API endpoint for serving. 
* Function utils/core_restapi_score.py provides the necessary methods to call REST API endpoint. This particular one is customized for SAS managed python/R models.

### Pre-requisites for EKS and required permissions

* A working EKS cluster that can scale up and down if required based on number of pods serving file scoring requests.
* IAM user and role settings - A IAM user id and key /creds should have permissions to create/delete lambda functions, lambda layers and ownership of S3 buckets to create event notifications. These are specified in config.properties under [AWS] section. 
* A Lambda role which is assumed by lambda function during execution. This should have permission to create/delete pods on K8S/EKS cluster, access to create pre-signed S3 URLS. Specified in config.properties under [lambda] section. 
* VPC/NATinPublicsubnet/LambdainPrivate – By default Lambda operates w/o VPC attached but that poses problem and we need to attach it to a VPC. See Appendix 1 – Decisions taken.
* Nginx-Ingress controller over AWS ALB or K8S default Load Balancer – See why we need this in Appendix 1 – Decisions taken. You also need to key in ingress URL in config.properties under [AWS] section 
* I AM user we created is going to let us talk to AWS only and not EKS yet. Amazon EKS uses IAM to provide authentication to your Kubernetes cluster but it still relies on native Kubernetes RBAC (Role-based access control) for authorization.  This means that IAM is only used for authentication of valid IAM entities. All permissions for interacting with your Amazon EKS cluster’s Kubernetes API is managed through the native Kubernetes RBAC system. AWS provided app “aws-auth” provides integration between AWS IAM and EKS RBAC access. EKS and this setup is done outside python codebase. 

### Setup Lambda function and S3 event notification on S3 bucket

* Log to an instance with python 3.7+ installed along with AWS boto3 packages installed. 
* git clone https://github.com/nallagangus/sasmm_modops.git
* cd awsdest
* ./setup_lambda_scoring_layer.py ==> Creates a lambda layer and returns ARN and version number that needs to be plugged in config.properties
* ./setup_lambda_scoring create default_fallback_modelimage s3://bucket1/folder1/folder2 s3://bucket2/folder_out 
  * creates a AWS lambda trigger with event notification on s3 bucket1/folder1/folder2 and output files destination is last parameter.

### Usage/Invocation part

* This is the easy part. All code has already been setup in proper places and there is no need to call any function. User just need to drop a file into s3://bucket1/folder1/folder2 and output will show in s3://bucket2/folder_out. 
* Attach "model_image and cluster specific info" to s3 object tags as part of object.For example 
  * $ aws s3api put-object –bucket bucket1 –key folder1/folder2/test1.csv –tagging ‘modelimagename=modelXYZ&k8scluster=fsbu-eks-east-1&k8snamespace=default&k8singressurl=http://a45e328847d8f11eab0fe0e927460cda-1444037516.us-east-1.elb.amazonaws.com’ –body test1.csv




## Implementation details
* AWS Boto3 is used for all interactions to AWS.
* AWS Boto3 is not suffice for pod, service and ingress interactions with Kubernetes. Kubernetes python client is used for those inetrafces. https://github.com/kubernetes-client/python/tree/master/kubernetes 

* *Why Nginx-Ingress controller over AWS ALB Ingress Controller?*
  * AWS ALB Ingress is good but it creates a new AWS ALB (EC2 LB) for every service which means it for every S3 file it will create a new AWS Asset Load Balancer and also a new Static IP Address. With 20+ files being dumped into our S3 folder it will generate 20+ Load Balancer (ec2 instance out there) and 20+ static addresses getting us out of Account limits. It simply will not scale for our needs. On otherhand, Ingress controller Load Balancer will use a single AWS Load Balancer and of course with just one static IP address.
  * We will use “Ingress rules” separate for each S3 file which directs to a specific POD we launched as part of this. We use “folder-structure-filename” to uniquely identify each POD (I could have used UUID but this is suffice for this purpose). Since scoring service on POD container can only take requests at predefined /executions and /query at root level – we need to do a URL “REWRITE” baked into ingress-rules so scoring service continues to receive at /executions and /query. URL Rewrite should handle rewrite of http://load-balancer/folder1-folder-2-filename/executions to http://POD/executions

* *Why Lambda in a VPC/Private subnet?*
  * Basically Lambda operates without any VPC( default) and so it’s exit IP address is random.  For Lambda function to make REST API calls to EKS Cluster we need to add Lambda Function IP Address to Nginx-Ingress LB Whitelist. Most organizations will not permit 0.0.0.0/0 as part of inbound rule to REST API calls. The only alternative is to get Lambda Function attached to a VPC but by definition of that it looses internet access which is required for accessing S3 presigned URL’s (among one). The right approach is defined at https://aws.amazon.com/premiumsupport/knowledge-center/internet-access-lambda-function/ To summarize above blog in few bullet points – Attach a “Lambda Function” to a VPC so it can get to internet access:
    1. Create a Public Subnet – Basically attach a IGW for destination 0.0.0.0/0
    2. Create an elastic IP address. We’ll use this with a NAT Gateway
    3. Create a new NAT Gateway and assign it to the Public Subnet and Elastic IP we created in the previous steps.
    4. Create a Private Subnet in our VPC and add a new route to the route table which routes to your NAT gateway for destinations 0.0.0.0/0. Get a new Route table to avoid conflicts.
    5. In your Lambda function, place the function within the private subnet(no public subnet) and choose a security group that lets you to internet w/o issues.
    6. Whitelist the NAT Elastic IP address in Nginx-Ingress LB Inbound rules
   
