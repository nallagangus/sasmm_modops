# Serverless AWS Lambda model scoring using containerized models and EKS

This project contains prototype implementation of scoring data/files in S3 using pre-trained models available as containers. 

This README describes the usecase background, architecture, pre-requisites for model container images/EKS and a user guide. It also discusses on how to extend for customer specific model container images. An Appendix refers to decisions made during implementation. 

## Use case

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
  * Leverage S3 as persistent storage for both input files and EC2 tags to carry process information.
  * Use EC2 tags and NOT S3 object metadata – as metadata is part of file itself and any updates of metadata results in rewriting entire file. It is a performance issue.
* *Scalable and Distributed*
  * Each file to get a dedicated container. Scaling is done at file level with each file getting a designated container for scoring.

![Image of scoring](https://github.com/nallagangus/sasmm_modops/AWS-Scoring-Lambda-Approach.jpg)

### Workflow
1. User drops a file into S3 bucket configured for event notifications
2. S3 Event notification triggers Lambda function for scoring
3. Our Custom developed Lambda Function (Assumes role lambdaexecutionrole) connects to Kubernetes API server to launch containers
4. K8S server connects with ECR to download SAS Proprietary Python/R Model image (OCI format) from ECR and launches “pod”. Each POD is unique to “file being scored”
5. Lambda function downloads S3 file locally
6. Makes a REST API call to Ingress Load Balancer which routes request to it’s specific POD
7. POD which hosts “model” scores against the file and sends “scored output back” to Lambda
8. Lambda gets file from POD and stores in S3 output bucket.



