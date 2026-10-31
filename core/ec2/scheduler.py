# decides whether the worker should run on an instance, and with which limits
# the chosen Order is sent back to the agent in the heartbeat response (see task_manager_api.py)
from typing import Optional

from pydantic import BaseModel

MAX_CPU_USED_PERCENT = 70  # only start a task if the client uses less than 70% of the cpu
STOP_CPU_USED_PERCENT = 90  # only stop a running task if the client goes above 90%: between 70 and 90 nothing changes, so tasks aren't killed on every small spike
RESERVED_CPU_PERCENT = 100 - MAX_CPU_USED_PERCENT  # share of the cpu always left to the client's application
RESERVED_MEM_MB = 512  # memory always left to the client's application
MIN_MEM_FREE_MB = 1024  # below this free memory, nothing is started
MIN_CPUS = 0.1  # below this, the container wouldn't have enough cpu to be useful

last_orders = {}  # last order sent to each instance, to know if a task is already running

task_iam_role = "arn:aws:iam::454492134174:role/gleaner-task-exemple" # TEMPORARY, AN LLM IS NEEDED TO CHOOSE THE TASK'S IAM ROLE, IT'S HARDCODED FOR NOW

# order sent back to the agent, must stay identical to the Order in agent/ec2/agent.py
class Order(BaseModel):
    action: str
    cpus: float = None
    memory_mb: int = None
    task_iam_role: Optional[str] = None  # IAM role the worker assumes for the task, chosen by core


def choose_container_action(instance_id, data):
    # tells the ec2 whether to start a task or not, based on the client's cpu and free ram

    client_cpu = data.client_cpu_percent  # sent by the agent, without our tasks' cpu
    client_memory = data.client_mem_free_mb  # sent by the agent, without our tasks' memory
    last_order = last_orders.get(instance_id)

    if client_memory < MIN_MEM_FREE_MB or client_cpu > STOP_CPU_USED_PERCENT:
        order = Order(action="stop")

    elif client_cpu >= MAX_CPU_USED_PERCENT:
        # between 70 and 90: keep the previous order (a running task continues, otherwise none is started)
        order = last_order if last_order is not None else Order(action="stop")
        
    else:
        cpus = round(data.vcpus * (100 - client_cpu - RESERVED_CPU_PERCENT) / 100, 2)
        if cpus < MIN_CPUS:
            order = Order(action="stop")
            
        else:
            order = Order(action="start", cpus=cpus, memory_mb=client_memory - RESERVED_MEM_MB, task_iam_role=task_iam_role)  # task_iam_role is hardcoded for now

    last_orders[instance_id] = order  # kept for the 70-90 zone of the next heartbeat
    return order
