# decide si le worker doit tourner sur une instance, et avec quelles limites
# l'Order choisi est renvoye a l'agent dans la reponse au heartbeat (voir task_manager_api.py)
from instances import instance_data
from pydantic import BaseModel

MAX_CPU_USED_PERCENT = 70  # au dela, l'instance n'a plus assez de marge pour recevoir des taches
RESERVED_CPU_PERCENT = 100 - MAX_CPU_USED_PERCENT  # part du cpu toujours laissee a l'application du client
RESERVED_MEM_MB = 512  # memoire toujours laissee a l'application du client
MIN_MEM_FREE_MB = 1024  # sous cette memoire libre, on ne lance rien
MIN_CPUS = 0.1  # en dessous, le conteneur n'aurait pas assez de cpu pour etre utile

# ordre renvoye a l'agent, doit rester identique a l'Order de agent/ec2/agent.py
class Order(BaseModel):
    action: str
    cpus: float = None
    memory_mb: int = None

def is_ready(data):
    cpu_used_percent = 100 - data.cpu_free_percent
    return cpu_used_percent < MAX_CPU_USED_PERCENT and data.mem_free_mb >= MIN_MEM_FREE_MB


def get_ready_instances():
    # instances qui utilisent moins de 70% de leur cpu, donc pretes a recevoir des taches
    ready_instances = []
    for instance_id, data in instance_data.items():
        if is_ready(data):
            ready_instances.append(instance_id)
    return ready_instances


def choose_container_action(data):
    # dit l'ec2 si elle doit lancer une tache ou pas, en fonction du cpu et et la ram libre
    if not is_ready(data):
        return Order(action="stop")

    cpus = round(data.vcpus * (data.cpu_free_percent - RESERVED_CPU_PERCENT) / 100, 2)
    if cpus < MIN_CPUS:
        return Order(action="stop")

    return Order(action="start", cpus=cpus, memory_mb=data.mem_free_mb - RESERVED_MEM_MB)
