# Agent gleaner : tourne sur l'EC2 du client
# envoie un heartbeat a core, et execute l'ordre que core renvoie dans la reponse
# fonctionnement complet : docs/architecture.md
import os
import threading
import time
from typing import Optional

import docker
import psutil
import requests
from pydantic import BaseModel

from worker import Worker



# le worker tourne dans un thread a part : une unite longue ne bloque jamais les heartbeats
worker = Worker()
threading.Thread(target=worker.run_forever, daemon=True).start()


# Configuration par variables d'environnement
CORE_URL = os.environ.get("CORE_URL", "http://localhost:8000")
INSTANCE_ID = os.environ.get("INSTANCE_ID", "local-dev")  # sur une vraie EC2, viendra de l'IMDS plus tard
INTERVAL = float(os.environ.get("INTERVAL", "5"))  # secondes entre deux envois

# message envoye a core, doit rester identique au HeartbeatData de core/ec2/task_manager_api.py
class HeartbeatData(BaseModel):
    cpu_free_percent: float
    mem_free_mb: int
    vcpus: int
    timestamp: int
    period_seconds: float  # durée couverte par la mesure

# ordre recu de core dans la reponse au heartbeat, doit rester identique a l'Order de core/ec2/scheduler.py
class Order(BaseModel):
    action: str
    cpus: Optional[float] = None  # Optional : core envoie null pour un stop
    memory_mb: Optional[int] = None # Optional : core envoie null pour un stop

def get_free_resources(period_seconds):
    # interval=None : moyenne depuis l'appel précédent, donc toute la période est mesurée
    cpu_used = psutil.cpu_percent(interval=None)
    return HeartbeatData(
        cpu_free_percent=round(100 - cpu_used, 1),
        mem_free_mb=psutil.virtual_memory().available // (1024 * 1024),
        vcpus=psutil.cpu_count(),
        timestamp=int(time.time()),
        period_seconds=round(period_seconds, 2),
    )


def send_heartbeat(data: HeartbeatData):
    # envoie les mesures a core et retourne l'Order qu'il renvoie, ou None si core est injoignable
    url = f"{CORE_URL}/heartbeat/{INSTANCE_ID}"
    try:
        response = requests.post(url, json=data.model_dump(), timeout=3)
        response.raise_for_status()
    except requests.RequestException as e:
        # core injoignable : on continue, on réessaiera au prochain tour
        print(f"Échec de l'envoi à core : {e}", flush=True)
        return None
    print(f"Envoyé : {data}", flush=True)
    return Order.model_validate(response.json())  # convertit l'action que il doit faire qui est dans "response" en objet Order et la return pour que la boucle "while True" puisse savoir quelle action faire



# Premier appel pour démarrer la mesure, sa valeur n'a pas de sens
psutil.cpu_percent(interval=None)
last_measure = time.monotonic()

while True:
    time.sleep(INTERVAL)
    now = time.monotonic()
    order: Order = send_heartbeat(get_free_resources(now - last_measure))
    last_measure = now

    if order is not None:
        print(f"Ordre reçu : {order}", flush=True)
        
        try:
            if order.action == "start":
                worker.start(order.cpus, order.memory_mb)

            elif order.action == "stop":
                worker.stop()

        except docker.errors.DockerException as e:
            # docker indisponible : on continue, l'ordre sera renvoye au prochain heartbeat
            print(f"Échec de l'ordre {order.action} : {e}", flush=True)