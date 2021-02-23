from os import path
from urllib.parse import urlparse

import requests
import json
import yaml
import xml.etree.ElementTree as ET
import pickle
import time
import sys

from awsdest.utils.core_aws import *

def validate_pingpong_on_scoring(k8s_cluster_url_for_scoring):
    try:
        http_resp = requests.get(url=k8s_cluster_url_for_scoring,timeout=5)
        #print("http response: ", http_resp )
        if http_resp.text == "pong":
            return True
        else:
            return False
    except:
        #print("No response from scoring service in validate_pingpong_on_scoring method:")
        #print(http_resp)
        return False

def _get_s3file_list(awsconfig,s3folderin):
    s3file_list = []
    try:
        response = requests.get(generate_presigned_url_for_list(awsconfig, s3folderin))
        root = ET.fromstring(response.content)
        for child in root.findall('{http://s3.amazonaws.com/doc/2006-03-01/}Contents'):
            Key = child.find('{http://s3.amazonaws.com/doc/2006-03-01/}Key')
            if (Key.text[-1] == "/"):  # skip directory level
                continue
            #print("Scoring File :", Key.text)
            s3file_list.append(Key.text)
        return s3file_list
    except:
        print("No response from AWS S3 predefined URL in _get_s3file_list method ")


def _score_one_file(session_id, awsconfig, s3folderin, file, k8s_url_for_scoring):
    s3file_downloadurl = generate_presigned_url_for_getobject(awsconfig, "get_object", s3folderin, file)
    try:
        r = requests.get(s3file_downloadurl,stream=True)
        filename = path.basename(file)
        with open("tmp/"+filename,"wb") as s3csv:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    s3csv.write(chunk)
        filename1 = {"file": open("tmp/"+filename,'rb') }
        http_response = session_id.post(url=k8s_url_for_scoring + "/executions",files=filename1)
        return http_response.text
    except:
        print("No response from one of requests in _score_one_file ")

def _get_scoring_result(session_id, file, scoring_token, k8s_url_for_scoring):
    query_url = k8s_url_for_scoring + "/query/" + scoring_token
    try:
        r = session_id.get(query_url,stream=True)
        if not r.status_code == 200 :
            return "scoring_progress"
        filename = path.basename(file)
        with open("tmp/" + filename + ".scoreout", "wb") as s3csv:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    s3csv.write(chunk)
        print("Retrieving scored output for :", file)
        return "scoring_completed"
    except:
        print("Unexpected error:", sys.exc_info())
        print("not proper response in _get_scoring_result method")
        return "scoring_progress"


def _upload_scoreout_one_file(session_id, awsconfig,s3folderout, file):
    parsed = urlparse(s3folderout, allow_fragments=False)
    prefix = parsed.path.lstrip('/')
    objkey = prefix + path.basename(file)+".scoreout"
    #print("objkey: ", objkey)
    s3file_uploadurl = generate_presigned_url_for_postobject(awsconfig, s3folderout, objkey)
    #print("s3 file upload URL", s3file_uploadurl)
    try:
        filename1 = {"file": open("tmp/"+path.basename(file)+".scoreout",'rb') }
        http_response = requests.post(url=s3file_uploadurl['url'],data=s3file_uploadurl['fields'],files=filename1)
        if not http_response.status_code in range(200,290):
            print("file upload to s3 failed", http_response.status_code)
            print("Full response:", http_response.text)
        print("scored output put to S3:", objkey)
        return http_response.status_code
    except:
        print("No response from one of requests _upload_scoreout_one_file to s3 ")
        print ("http response from uploading to s3:", http_response.text)
        return http_response.status_code

def score_s3files_controller(awsconfig,s3folderin,s3folderout,k8s_url_for_scoring, maxtime_scoring):
    # build a session_list structure that has 4 tuples for each entry. This 4 tuple helps us maintain control over job execution
    # 1. session_id to help cookie and session affinity 2. file - name of file we are dealing
    # 3. token - used to check back on status from scoring service 4. actual status of job

    print("Scoring process started at ", time.ctime(time.time()))

    sessions_list = []
    s3file_list = _get_s3file_list(awsconfig,s3folderin)

    # Phase1 - submit scoring job for each file. All scoring is done in parallel with a fire-and-forget approach
    # but do save scoring_token from each submission. We need these to go back and retreive scored output in phase1
    for file in s3file_list:
        session_id = requests.session()
        scoring_result = _score_one_file(session_id, awsconfig, s3folderin, file, k8s_url_for_scoring)
        scoring_token = json.loads(scoring_result)["id"]
        sessions_list.append((session_id, file, scoring_token, "scoring_started"))
        print("Set for scoring file, scoring_token, sessionid :", file, scoring_token, session_id)

    # for debug purposes save as pickle file and then reload
    #print("session list:", sessions_list )
    #with open("tmp/session_list.dat", "wb") as f:
    #    pickle.dump(sessions_list, f)

    # phase2 - check status of each scoring job. # wait on this until all jobs are complete.
    jobs_pending = True
    start = time.time()
    while jobs_pending and (time.time() - start < maxtime_scoring):
        jobs_pending = False # just set it to false if any of jobs is still in progress we will set it back to True
        for session_item in range(len(sessions_list)):
            session_id, file, scoring_token, scoring_status = sessions_list[session_item]
            if not scoring_status == "scoring_completed":
                scoring_status = _get_scoring_result(session_id, file, scoring_token, k8s_url_for_scoring)
            if not scoring_status == "scoring_completed":
                jobs_pending = True
            sessions_list[session_item] = ( session_id, file, scoring_token, scoring_token)


    if jobs_pending:
        print("Went beyond allocated time. Aborting scoring ")
        print("Time limit for scoring in secs: ",maxtime_scoring, "start time: ", time.ctime(start), "current time: ", time.ctime(time.time()))
        return 1

    # phase-3  upload each scored output file to s3
    for session_item in range(len(sessions_list)):
        session_id, file, scoring_token, scoring_status = sessions_list[session_item]
        if not _upload_scoreout_one_file(session_id, awsconfig, s3folderout, file) in range (200,299):
            return 1

    print("Scoring process Ended at ", time.ctime(time.time()))



if __name__ == '__main__':
    validate_pingpong_on_scoring("http://ab9f8139f783911eabed40a902ea44af-1673116921.us-east-1.elb.amazonaws.com:8080/")







