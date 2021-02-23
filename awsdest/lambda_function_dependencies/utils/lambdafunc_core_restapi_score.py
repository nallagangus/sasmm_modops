from os import path

import requests
import json
import yaml
import xml.etree.ElementTree as ET
import pickle
import time
import sys
import os

from utils.lambdafunc_core_aws import *


def validate_pingpong_on_scoring(k8s_cluster_url_for_scoring):
    try:
        http_resp = requests.get(url=k8s_cluster_url_for_scoring, timeout=10)
        print("Ingress ping pong response: ", http_resp.text)
        if http_resp.text == 'pong':
            return True
        else:
            return False
    except:
        print("No response from scoring service in validate_pingpong_on_scoring method:")
        print("Unexpected error:", sys.exc_info())
        return False


###

def upload_file_s3(s3_presigned_url, filename=None, text_message=None):
    # print("s3 file upload URL", s3file_uploadurl)
    try:
        # either filename or text_message or used by clients.
        if text_message is not None:
            err_filename = '/tmp/err_text'
            with open(err_filename, 'w+') as f:
                f.write(text_message)
            file_r = {"file": open(err_filename, 'r')}
        else:
            filename1 = "/tmp/" + filename
            file_r = {"file": open(filename1, 'rb')}
    except:
        err_message = str(sys.exc_info())
        return (560, err_message)

    try:
        http_response = requests.post(url=s3_presigned_url['url'], data=s3_presigned_url['fields'], files=file_r)
        if not http_response.status_code in range(200, 290):
            print("file upload to s3 failed", http_response.status_code)
            print("Full response:", http_response.text)

        return (0, http_response.status_code)
    except:
        error_message = str(sys.exc_info())
        return (570, "error uploading file to S3" + error_message)


###--

def _download_one_file(s3file_downloadurl, unique_env_idenitier):
    try:
        r = requests.get(s3file_downloadurl, stream=True)
        filename = unique_env_idenitier
        with open("/tmp/" + filename, "wb") as s3csv:
            # print(" file opened for writing")
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    s3csv.write(chunk)
        return (0, "success")
    except:
        err_mesg1 = "Error downloading file from S3 "
        err_mesg2 = str(sys.exc_info())
        return (510, err_mesg1 + err_mesg2)


def _score_one_file(k8s_url_for_scoring, unique_env_identifier):
    try:
        filename1 = {"file": open("/tmp/" + unique_env_identifier, 'rb')}
        http_response = requests.post(url=k8s_url_for_scoring + "/executions", files=filename1)
        scoring_token = json.loads(http_response.text)["id"]
        # print("scoring response token:", scoring_token)
        return (0, scoring_token)
    except:
        err_mesg1 = "Error submitting to scoring url @" + k8s_url_for_scoring + "/executions"
        err_mesg2 = str(sys.exc_info())
        return (520, err_mesg1 + err_mesg2)


def _get_scoring_result(k8s_url_for_scoring, unique_env_identifier, scoring_token):
    query_url = k8s_url_for_scoring + "/query/" + scoring_token
    try:
        filename = unique_env_identifier
        r = requests.get(query_url, stream=True)
        if not r.status_code == 200:
            return (530, "scoring_progress")

        with open("/tmp/" + filename + ".scoreout", "wb") as s3csv:
            # print("opened scoreout for writing")
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    s3csv.write(chunk)
        # print("Retrieving scored output for :", filename)
        return (0, "scoring_completed")
    except:
        err_mesg = str(sys.exc_info())
        return (540, "scoring_result method failed" + err_mesg)


def score_file_process(s3infile_presignedurl, s3outfile_presignedurl, k8s_url_for_scoring, unique_env_identifier,
                       maxtime_scoring=40):
    download_status_code, message = _download_one_file(s3infile_presignedurl, unique_env_identifier)
    if not download_status_code == 0:
        return (download_status_code, message)

    score_status_code, scoring_token = _score_one_file(k8s_url_for_scoring, unique_env_identifier)
    print("score status code and token:", score_status_code, scoring_token)
    if not score_status_code == 0:
        return (score_status_code, scoring_token)

    jobs_pending = True
    start = time.time()
    while jobs_pending and (time.time() - start < maxtime_scoring):
        scoring_result_code, message = _get_scoring_result(k8s_url_for_scoring, unique_env_identifier, scoring_token)
        # print("scoring result code:", scoring_result_code)
        if scoring_result_code == 0:
            jobs_pending = False

    if jobs_pending:
        print("Went beyond allocated time. Aborting scoring ")
        print("Time limit for scoring in secs: ", maxtime_scoring, "start time: ", time.ctime(start), "current time: ",
              time.ctime(time.time()))
        return (550, "Went beyond allocated time of 720secs for scoring")

    # print("Scoring completed. File upload to begin..")
    upload_status_code, message = upload_file_s3(s3outfile_presignedurl, filename=unique_env_identifier + ".scoreout")
    if not upload_status_code == 0:
        return (570, message)

    return (0, "scoring completed and file uploaded to S3")

