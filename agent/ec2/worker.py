# gleaner worker: reads tasks from SQS and runs the code of each one in the generic gleaner image
# the agent drives it with start() / stop() based on core's orders
import json
import os
import time

import boto3
import docker


QUEUE_URL = os.environ["QUEUE_URL"]
TASK_IMAGE = os.environ["TASK_IMAGE"]  # generic gleaner image (Python + boto3 + Pillow) that runs the code written by the LLM
TASK_NAME = "gleaner-task"  # fixed name, so the agent can find the container
TASK_NETWORK = "gleaner-tasks"  # internal docker network: no way out, neither internet nor the machine's metadata
PROXY_NAME = "gleaner-proxy"  # the task's only way out, see proxy.py
PROXY_URL = "http://gleaner-proxy:3128"  # docker resolves the container's name on the task network
TASK_MAX_SECONDS = 840  # 14 min: under the 15 min of the task's credentials and of the SQS visibility timeout

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


def create_task_network():
    # created once per machine; internal=True: docker gives it no route out, whatever the client's EC2 settings
    for network in docker_client.networks.list(names=[TASK_NETWORK]):
        if network.name == TASK_NETWORK:  # the name filter also matches longer names
            return
    docker_client.networks.create(TASK_NETWORK, driver="bridge", internal=True)


def s3_resource_to_hosts(resource):
    # "s3://bucket/photos/" -> the bucket's two addresses (global and regional), the ones boto3 uses for it
    bucket = resource[len("s3://"):].split("/")[0]
    region = os.environ["AWS_DEFAULT_REGION"]
    return [f"{bucket}.s3.amazonaws.com", f"{bucket}.s3.{region}.amazonaws.com"]


def start_proxy(resources):
    # one proxy per task, which lets through only HTTPS to the task's buckets
    allowed_hosts = []
    for resource in resources:
        allowed_hosts += s3_resource_to_hosts(resource)

    try:
        # proxy left by a previous task or agent: otherwise its name blocks the new one
        docker_client.containers.get(PROXY_NAME).remove(force=True)
    except docker.errors.NotFound:
        pass

    # started on docker's default network, which has a way out, then connected to the task network
    proxy = docker_client.containers.run(
        TASK_IMAGE,
        ["python", "proxy.py"],
        name=PROXY_NAME,
        user="nobody",
        detach=True,
        environment={"ALLOWED_HOSTS": ",".join(allowed_hosts)},
    )
    docker_client.networks.get(TASK_NETWORK).connect(proxy)

    # the task must not start before the proxy listens
    for attempt in range(50):
        if b"ready" in proxy.logs():
            return proxy
        time.sleep(0.1)
    raise RuntimeError("le proxy n'a pas démarré")


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

        create_task_network()

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
                    proxy = start_proxy(unit["input_resources"] + unit["output_resources"])

                    try:
                        # task left by a run that failed halfway: otherwise its name blocks this one
                        docker_client.containers.get(TASK_NAME).remove(force=True)
                    except docker.errors.NotFound:
                        pass

                    # started in the background: the worker watches its duration
                    task = docker_client.containers.run(
                        TASK_IMAGE,
                        ["python", "-c", unit["code"]],  # the task code travels in the message, validated by a reviewer
                        name=TASK_NAME,
                        user="nobody",  # the image runs as root for the agent: the LLM's code must not
                        network=TASK_NETWORK,  # no route out: neither internet nor the machine's metadata
                        detach=True,
                        read_only=True,  # the task can't change its image (its code, python, its libraries)
                        tmpfs={"/tmp": f"size={memory_mb // 2}m"},  # the only place it can write, in memory, so counted in its memory limit
                        cap_drop=["ALL"],  # no linux capability, even the ones docker gives by default
                        security_opt=["no-new-privileges"],  # nothing it runs can gain more rights than nobody
                        pids_limit=100,  # at most 100 processes: no fork bomb
                        blkio_weight=10,  # minimum disk priority (500 by default), like cpu_shares for the cpu
                        environment={
                            "GLEANER_TASK": message["Body"],
                            "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
                            "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
                            "AWS_SESSION_TOKEN": credentials["SessionToken"],
                            "AWS_DEFAULT_REGION": os.environ["AWS_DEFAULT_REGION"],
                            "HTTPS_PROXY": PROXY_URL,  # boto3 sends its requests through it on its own
                        },
                        nano_cpus=int(cpus * 1e9),  # equivalent of --cpus
                        mem_limit=f"{memory_mb}m",  # equivalent of --memory
                        cpu_shares=2,  # minimum cpu priority (1024 by default): under contention, the kernel gives the cpu to the client's application
                        oom_score_adj=1000,  # if memory runs out, the kernel kills the task first, never the client's application
                    )

                    # 1. wait for the end, at most TASK_MAX_SECONDS (a stop() from core also ends it)
                    deadline = time.time() + TASK_MAX_SECONDS
                    task.reload()
                    while task.status in ("created", "running") and time.time() < deadline:
                        time.sleep(1)
                        task.reload()
                    if task.status in ("created", "running"):
                        task.kill()  # too long: its credentials expire at 15 min
                        print("Tâche trop longue, arrêtée", flush=True)

                    # 2. exit code 0: done; anything else (failed, killed): keep the message, SQS will deliver it again after the visibility timeout
                    exit_code = task.wait()["StatusCode"]
                    task.remove()
                    proxy.remove(force=True)
                    if exit_code == 0:
                        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])
                        print("Tâche terminée", flush=True)
                    else:
                        print("Tâche interrompue, elle sera refaite", flush=True)
            except Exception as e:
                # SQS unreachable, image not found...: the thread must never die
                print(f"Erreur du worker : {e}", flush=True)
                time.sleep(5)
    

