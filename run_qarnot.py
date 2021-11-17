#!/usr/bin/env python3

import time
import qarnot
import logging
import botocore.exceptions
from logging.config import fileConfig

def submit_task(param_dict):

    # Configure logger
    fileConfig("python_logging.conf")
    logger = logging.getLogger()
    logger.debug("Logger configured successfully")

    try:
        logger.debug("Connecting to Qarnot API...")
        conn = qarnot.Connection(client_token=param_dict['token'])

        logger.debug("Creating task...")
        task = conn.create_task(param_dict['task'], 'jupyterlab', 1)

        # Create an input bucket and attach it to the task
        logger.debug("Provisionning input bucket...")
        input_bucket = conn.create_bucket(param_dict['in_bucket'])
        input_bucket.sync_directory('input_binder/')
        task.resources.append(input_bucket)

        # Create a result bucket and attach it to the task
        output_bucket = conn.retrieve_or_create_bucket(param_dict['out_bucket'])
        task.results = output_bucket

        # Fill in task constants from the notebook form
        task.constants['DOCKER_SSH'] = param_dict['ssh_key']
        task.constants['DOCKER_REPO'] = param_dict['soft']
        task.constants['DOCKER_TAG'] = "v1"

        task.snapshot(5)

        # Initial values for forward port and ssh tunneling bool
        ssh_forward_port = None
        # Bool for tracking ssh and link status
        ssh_done = False
        link_done = False
        last_state = ''
        jupyter_link = ''
        remote_log='logs/qarnot.log'
        # substring to look for in logs
        token = 'http://localhost:8888/lab?token='

        # Delete old log file if present (to avoid fetching previous token)
        try:
            logger.debug("Purging previous logs...")
            if list(output_bucket.directory(directory='logs/'))[0].key==remote_log:
                output_bucket.delete_file(remote_log)
        except IndexError:
            logger.debug("No logs found...")

        # Append previous output bucket to inputs if bool is true
        if param_dict['use_output_bucket']:
            task.resources.append(conn.retrieve_bucket(param_dict['prev_out_bucket']))

        # Submit the task
        logger.debug("Launching task...")    
        task.submit()
        uuid = task.uuid

        while not (link_done and ssh_done):
            if task.state != last_state:
                last_state = task.state
                logger.debug(last_state)

            if task.state == 'FullyExecuting':

                active_forward = task.status.running_instances_info.per_running_instance_info[0].active_forward
                # If the ssh connexion was not done yet and the list active_forward is available (len!=0)
                if len(active_forward)!=0 and not ssh_done:
                    logger.debug("Fetching forward port...")
                    ssh_forward_port = active_forward[0].forwarder_port
                    ssh_done=True

                # Fetch Jupyter link with secret token from logs on Qarnot
                if not link_done:
                    try:
                        jupyter_link = get_link(output_bucket, token, remote_log)
                    except (FileNotFoundError, IndexError, botocore.exceptions.ClientError):
                        logger.debug("Link not available yet, retrying...")
                        time.sleep(5)
                    if token in jupyter_link:
                        logger.debug("Link ready...")
                        link_done = True
            
        logger.debug("JupyterLab is ready...")
        return (ssh_forward_port, jupyter_link, uuid)
    
    except Exception:
        logger.exception("An exception occured.")

def get_link(output_bucket, token, remote_log):
    log_file='outputs_binder/'+remote_log
    output_bucket.get_file(remote=remote_log, local=log_file)
    results=[]
    with open(log_file, 'r') as myFile:
        for line in myFile:
            if token in line and len(results)<2:
                results.append(line.strip())
    return results[1]

def abort_task(qarnot_token, uuid):
    conn = qarnot.Connection(client_token=qarnot_token)
    try:
        task = conn.retrieve_task(uuid)
        print("Retrieving results...")
        task.instant()
        task.download_results('outputs_binder')
        print("Killing task...")
        task.abort()
    except qarnot.exceptions.MissingTaskException:
        print("No task to kill...")