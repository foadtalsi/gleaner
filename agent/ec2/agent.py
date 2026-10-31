# gleaner agent: runs on the client's EC2
# sends a heartbeat to core, and executes the order core sends back in the response
# full walkthrough: docs/architecture.md
import os
import threading
import time
from typing import Optional

import docker
import psutil
import requests
from pydantic import BaseModel

from worker import Worker



# the worker runs in a separate thread: a long unit never blocks the heartbeats
worker = Worker()
threading.Thread(target=worker.run_forever, daemon=True).start()


# Configuration through environment variables
CORE_URL = os.environ.get("CORE_URL", "http://localhost:8000")
INSTANCE_ID = os.environ.get("INSTANCE_ID", "local-dev")  # on a real EC2, will come from the IMDS later
INTERVAL = float(os.environ.get("INTERVAL", "5"))  # seconds between two sends
CORE_TIMEOUT = float(os.environ.get("CORE_TIMEOUT", "15"))  # seconds without a response from core before stopping the task

AGENT_KEY = "agent_1_key" # later it will be fetched from dynamodb, not hardcoded in the code


# message sent to core, must stay identical to HeartbeatData in core/ec2/task_manager_api.py
class HeartbeatData(BaseModel):
    cpu_free_percent: float
    vcpus: int
    period_seconds: float  # duration covered by the measurement
    client_cpu_percent: float  # % of the machine's cpu used by the client's application (without our tasks)
    client_mem_free_mb: int  # memory that would be free if our tasks were not there
    agent_key: str  # so core knows the heartbeat really comes from the agent, and not from an intruder

# order received from core in the heartbeat response, must stay identical to the Order in core/ec2/scheduler.py
class Order(BaseModel):
    action: str
    cpus: Optional[float] = None  # Optional: core sends null for a stop
    memory_mb: Optional[int] = None # Optional: core sends null for a stop
    task_iam_role: Optional[str] = None  # IAM role the task receives, chosen by core

# -------------------------------------------------------------------------------------------------------------------------


def get_instance_data(period_seconds): # gives the instance's measurements, for the heartbeat sent to core
    cpu_used = psutil.cpu_percent(interval=None)
    memory_free = psutil.virtual_memory().available // (1024 * 1024)  # converts to MB

    task_cpu, task_memory = get_task_usage()  # what our tasks use, so core doesn't confuse it with the client's application

    # max(0): the two measurements don't cover the same duration (whole period / ~1 s), the difference can be negative
    client_cpu_used = max(0, cpu_used - task_cpu) # cpu used by the client's application, without our tasks
    client_memory_free = memory_free + task_memory # memory that would be free without our tasks (the memory they use is given back as soon as we stop them)

    return HeartbeatData(
        cpu_free_percent=round(100 - cpu_used, 1),
        vcpus=psutil.cpu_count(),
        period_seconds=round(period_seconds, 2),
        client_cpu_percent=round(client_cpu_used, 1),
        client_mem_free_mb=client_memory_free,
        agent_key=AGENT_KEY
    )


def send_heartbeat(data: HeartbeatData):
    # sends the measurements to core and returns the Order it sends back, or None if core is unreachable
    url = f"{CORE_URL}/heartbeat/{INSTANCE_ID}"
    
    try:
        response = requests.post(url, json=data.model_dump(), timeout=3)
        response.raise_for_status()
    except requests.RequestException as e:
        # core unreachable: keep going, we'll retry on the next round
        print(f"Échec de l'envoi à core : {e}", flush=True)
        return None
    print(f"Envoyé : {data}", flush=True)
    return Order.model_validate(response.json())  # converts the action to perform, which is in "response", into an Order object and returns it so the "while True" loop knows which action to take


def get_task_usage(): # returns (% of the machine's CPU, memory in MB) used by the task, 0 for what docker doesn't measure
    try:
        stats = docker.from_env().containers.get("gleaner-task").stats(stream=False)  # a single call: it takes ~1 s
    except docker.errors.NotFound:
        return 0, 0  # no task running

    task_cpu_percent = 0
    # docker gives two readings ~1 s apart: "precpu_stats" (before) and "cpu_stats" (after)
    after = stats["cpu_stats"]
    before = stats["precpu_stats"]
    if "system_cpu_usage" in after and "system_cpu_usage" in before:  # missing if the task has already finished (or has just started)
        task_cpu = after["cpu_usage"]["total_usage"] - before["cpu_usage"]["total_usage"]  # cpu time used by the task
        machine_cpu = after["system_cpu_usage"] - before["system_cpu_usage"]  # total cpu time of the machine
        task_cpu_percent = round(task_cpu / machine_cpu * 100, 1)

    task_memory_mb = 0
    if "usage" in stats["memory_stats"]:  # missing if the task has already finished
        task_memory_mb = stats["memory_stats"]["usage"] // (1024 * 1024)  # converts to MB

    return task_cpu_percent, task_memory_mb


# First call to start the measurement, its value is meaningless
psutil.cpu_percent(interval=None)
last_measure = time.monotonic()
last_core_response = time.monotonic()  # last time core responded



while True:
    time.sleep(INTERVAL)
    now = time.monotonic()
    order: Order = send_heartbeat(get_instance_data(now - last_measure)) # this is where we send the heartbeat and receive core's order
    last_measure = now

    if order is not None:
        last_core_response = now
    elif now - last_core_response > CORE_TIMEOUT and worker.limits is not None:
        # core unreachable for too long: nobody is watching the machine anymore, stop the task to be safe
        # it will resume when core replies start
        print(f"Core injoignable depuis plus de {CORE_TIMEOUT:.0f} s : arrêt de la tâche par sécurité", flush=True)
        order = Order(action="stop")

    if order is not None:
        print(f"Ordre reçu : {order}", flush=True)
        
        try:
            if order.action == "start":
                worker.start(order.cpus, order.memory_mb, order.task_iam_role)

            elif order.action == "stop":
                worker.stop()

        except docker.errors.DockerException as e:
            # docker unavailable: keep going, the order will be sent again on the next heartbeat
            print(f"Échec de l'ordre {order.action} : {e}", flush=True)