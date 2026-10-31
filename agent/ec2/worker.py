# gleaner worker: reads tasks from SQS and runs the engineer's image for each one
# the agent drives it with start() / stop() based on core's orders
import json
import os
import time

import boto3
import docker


QUEUE_URL = os.environ["QUEUE_URL"]
TASK_IMAGE = os.environ["TASK_IMAGE"]  # engineer's image
TASK_COMMAND = os.environ.get("TASK_COMMAND")  # None: keep the image's CMD
TASK_NAME = "gleaner-task"  # fixed name, so the agent can find the container

sqs = boto3.client("sqs")
sts = boto3.client("sts")
docker_client = docker.from_env()


def s3_resource_to_arn(resource):
    # "s3://bucket/photos/" -> "arn:aws:s3:::bucket/photos/*"
    return "arn:aws:s3:::" + resource[len("s3://"):] + "*"


def build_session_policy(input_resources, output_resources):
    # the session policy cuts the role's rights down to the declared resources: read the inputs, write the outputs, nothing else
    read_arns = []
    for resource in input_resources:
        read_arns.append(s3_resource_to_arn(resource))

    write_arns = []
    for resource in output_resources:
        write_arns.append(s3_resource_to_arn(resource))

    # AWS refuses a statement with an empty resource list, e.g. a read-only task has no outputs
    statements = []
    if len(read_arns) > 0:
        statements.append({"Effect": "Allow", "Action": "s3:GetObject", "Resource": read_arns})
    if len(write_arns) > 0:
        statements.append({"Effect": "Allow", "Action": "s3:PutObject", "Resource": write_arns})

    return json.dumps({"Version": "2012-10-17", "Statement": statements})


class Worker:
    def __init__(self):
        self.limits = None  # (cpus, memory_mb) received from core, None = paused
        self.task_iam_role = None  # task's IAM role, received from core in the Order

    def start(self, cpus, memory_mb, task_iam_role):
        # the new limits apply from the next task on
        self.limits = (cpus, memory_mb)
        self.task_iam_role = task_iam_role

    def stop(self):
        self.limits = None
        try:
            docker_client.containers.get(TASK_NAME).kill()  # hard stop, the message will come back to SQS
        except docker.errors.APIError:
            pass  # no task running

    def run_forever(self):
        try:
            # task left by a previous agent (update, crash...): otherwise its name blocks all following tasks
            docker_client.containers.get(TASK_NAME).remove(force=True)
            print("Tâche orpheline supprimée, son message reviendra dans la file", flush=True)
        except docker.errors.NotFound:
            pass

        while True:
            if self.limits is None: # if limits has a value, agent.py received start() from core, so we can begin; if it's None, there is nothing and we wait 1 second
                time.sleep(1)  # paused, waiting for a start
                continue

            try:
                response = sqs.receive_message(QueueUrl=QUEUE_URL, WaitTimeSeconds=10)
                for message in response.get("Messages", []):
                    current = self.limits
                    if current is None:
                        break  # stop received while waiting, the message will come back to the queue
                    cpus, memory_mb = current

                    # the role and the resources come with each message, chosen by core and validated by a reviewer
                    unit = json.loads(message["Body"])
                    session_policy = build_session_policy(unit["input_resources"], unit["output_resources"])
                    # temporary credentials for the task's role, valid 15 min, limited by the session policy: only the task receives them
                    credentials = sts.assume_role(RoleArn=unit["iam_role"], RoleSessionName="gleaner-task", DurationSeconds=900, Policy=session_policy)["Credentials"]

                    try:
                        # blocks until the end, raises ContainerError if the exit code is not 0
                        docker_client.containers.run(
                            TASK_IMAGE,
                            TASK_COMMAND,
                            name=TASK_NAME,
                            remove=True,
                            environment={
                                "GLEANER_TASK": message["Body"],
                                "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
                                "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
                                "AWS_SESSION_TOKEN": credentials["SessionToken"],
                                "AWS_DEFAULT_REGION": os.environ["AWS_DEFAULT_REGION"],
                            },
                            nano_cpus=int(cpus * 1e9),  # equivalent of --cpus
                            mem_limit=f"{memory_mb}m",  # equivalent of --memory
                            cpu_shares=2,  # minimum cpu priority (1024 by default): under contention, the kernel gives the cpu to the client's application
                            oom_score_adj=1000,  # if memory runs out, the kernel kills the task first, never the client's application
                        )
                        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])
                        print("Tâche terminée", flush=True)
                    except docker.errors.ContainerError:
                        # failed or killed: keep the message, SQS will deliver it again after the visibility timeout
                        print("Tâche interrompue, elle sera refaite", flush=True)
            except Exception as e:
                # SQS unreachable, image not found...: the thread must never die
                print(f"Erreur du worker : {e}", flush=True)
                time.sleep(5)
    

