# decide si le worker doit tourner sur une instance, et avec quelles limites
# l'Order choisi est renvoye a l'agent dans la reponse au heartbeat (voir task_manager_api.py)
from instances import instance_data
from pydantic import BaseModel

MAX_CPU_USED_PERCENT = 70  # on ne lance une tache que si le client utilise moins de 70% du cpu
STOP_CPU_USED_PERCENT = 90  # on n'arrete une tache en cours que si le client depasse 90% : entre 70 et 90 on ne change rien, pour ne pas tuer des taches a chaque petit pic
RESERVED_CPU_PERCENT = 100 - MAX_CPU_USED_PERCENT  # part du cpu toujours laissee a l'application du client
RESERVED_MEM_MB = 512  # memoire toujours laissee a l'application du client
MIN_MEM_FREE_MB = 1024  # sous cette memoire libre, on ne lance rien
MIN_CPUS = 0.1  # en dessous, le conteneur n'aurait pas assez de cpu pour etre utile

last_orders = {}  # dernier ordre envoye a chaque instance, pour savoir si une tache tourne deja

# ordre renvoye a l'agent, doit rester identique a l'Order de agent/ec2/agent.py
class Order(BaseModel):
    action: str
    cpus: float = None
    memory_mb: int = None

def is_ready(data):
    return data.client_cpu_percent < MAX_CPU_USED_PERCENT and data.mem_free_mb >= MIN_MEM_FREE_MB


def get_ready_instances():
    # instances qui utilisent moins de 70% de leur cpu, donc pretes a recevoir des taches
    ready_instances = []
    for instance_id, data in instance_data.items():
        if is_ready(data):
            ready_instances.append(instance_id)
    return ready_instances


def choose_container_action(instance_id, data):
    # dit l'ec2 si elle doit lancer une tache ou pas, en fonction du cpu du client et de la ram libre
    client_cpu = data.client_cpu_percent  # envoye par l'agent, sans le cpu de nos taches
    last_order = last_orders.get(instance_id)

    if data.mem_free_mb < MIN_MEM_FREE_MB or client_cpu > STOP_CPU_USED_PERCENT:
        order = Order(action="stop")

    elif client_cpu >= MAX_CPU_USED_PERCENT:
        # entre 70 et 90 : on garde l'ordre precedent (une tache en cours continue, sinon on n'en lance pas)
        order = last_order if last_order is not None else Order(action="stop")
        
    else:
        cpus = round(data.vcpus * (100 - client_cpu - RESERVED_CPU_PERCENT) / 100, 2)
        if cpus < MIN_CPUS:
            order = Order(action="stop")
            
        else:
            order = Order(action="start", cpus=cpus, memory_mb=data.mem_free_mb - RESERVED_MEM_MB)

    last_orders[instance_id] = order  # retenu pour la zone 70-90 du prochain heartbeat
    return order
