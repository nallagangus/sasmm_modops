
from os import path
from urllib.parse import urlparse

import sys

import requests
import json
import yaml
import math
import boto3
from botocore.exceptions import ClientError
import time

if __name__ == '__main__':
    #k8s_cluster_url_for_scoring = "http://ab344038e67a111eab99602b0308bf38-291195657.us-east-1.elb.amazonaws.com:8080/"
    #score_model(k8s_cluster_url_for_scoring,"../test.csv")
    start = time.ctime()
    print("start time:", start)

    ss = "$2"
    st = "123"

    print(ss)
    print(st)







#    try:
#       filename1 = {"file": open("tmp/score1out", 'rb')}
#        http_response = requests.post(url=s3file_uploadurl['url'], data=s3file_uploadurl['fields'], files=filename1)
#        if not http_response.status_code in range(200, 290):
#            print("file upload to s3 failed", http_response.status_code)
#            print("Full response:", http_response.text)
#    except:
#        print("No response from one of requests _upload_scoreout_one_file to s3 ")
#        print ("http response from uploading to s3:", http_response.text)
#        return http_response.status_code

