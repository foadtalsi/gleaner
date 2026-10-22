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
CORE_TIMEOUT = float(os.environ.get("CORE_TIMEOUT", "15"))  # secondes sans reponse de core avant d'arreter la tache


# message envoye a core, doit rester identique au HeartbeatData de core/ec2/task_manager_api.py
class HeartbeatData(BaseModel):
    cpu_free_percent: float
    mem_free_mb: int
    vcpus: int
    timestamp: int
    period_seconds: float  # durée couverte par la mesure
    client_cpu_percent: float  # % du cpu de la machine utilise par l'application du client (sans nos taches)
    client_mem_free_mb: int = None  # memoire libre de l'application du client, si elle est connue (sinon None)

# ordre recu de core dans la reponse au heartbeat, doit rester identique a l'Order de core/ec2/scheduler.py
class Order(BaseModel):
    action: str
    cpus: Optional[float] = None  # Optional : core envoie null pour un stop
    memory_mb: Optional[int] = None # Optional : core envoie null pour un stop
    task_iam_role: Optional[str] = None  # role IAM que la tache recoit, choisi par core

# -------------------------------------------------------------------------------------------------------------------------


def get_instance_data(period_seconds): # donne les mesures de l'instance, pour le heartbeat a envoyer a core
    cpu_used = psutil.cpu_percent(interval=None)
    memory_free = psutil.virtual_memory().available // (1024 * 1024)  # convertit en Mo

    task_cpu = get_task_cpu_percent() or 0  # cpu de nos taches, pour que core ne le confonde pas avec celui du client
    task_memory = get_task_memory_mb() or 0  # memoire de nos taches, pour que core ne le confonde pas avec celle du client

    # max(0) : les deux mesures ne couvrent pas la meme duree (periode entiere / ~1 s), la difference peut etre negative
    client_cpu_left = max(0, cpu_used - task_cpu) # cpu libre de l'application du client, sans nos taches et les siennes
    client_memory_left = memory_free + task_memory # memoire qui serait libre sans nos taches (celle qu'elles occupent est rendue des qu'on les arrete)

    return HeartbeatData(
        cpu_free_percent=round(100 - cpu_used, 1),
        mem_free_mb=psutil.virtual_memory().available // (1024 * 1024),
        vcpus=psutil.cpu_count(),
        timestamp=int(time.time()),
        period_seconds=round(period_seconds, 2),
        client_cpu_percent=round(client_cpu_left, 1),
        client_mem_free_mb=client_memory_left
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


def get_task_cpu_percent(): # retourne le pourcentage de CPU de la machine pris par la task, ou None si elle n'est pas en cours
   
    try:
        stats = docker.from_env().containers.get("gleaner-task").stats(stream=False)
    except docker.errors.NotFound:
        return None

    # docker donne deux releves a ~1 s d'intervalle : "precpu_stats" (avant) et "cpu_stats" (apres)
    after = stats["cpu_stats"]
    before = stats["precpu_stats"]
    if "system_cpu_usage" not in after or "system_cpu_usage" not in before:
        return None  # tache deja terminee (ou tout juste lancee) : docker n'a pas de releve cpu

    task_cpu = after["cpu_usage"]["total_usage"] - before["cpu_usage"]["total_usage"]  # temps cpu pris par la tache
    machine_cpu = after["system_cpu_usage"] - before["system_cpu_usage"]  # temps cpu total de la machine

    return round(task_cpu / machine_cpu * 100, 1)

def get_task_memory_mb(): # retourne la memoire de la machine prise par la task, ou None si elle n'est pas en cours
    try:
        stats = docker.from_env().containers.get("gleaner-task").stats(stream=False)
    except docker.errors.NotFound:
        return None
    if "usage" not in stats["memory_stats"]:
        return None  # tache deja terminee : docker n'a plus de releve memoire

    return stats["memory_stats"]["usage"] // (1024 * 1024)  # convertit en Mo


# Premier appel pour démarrer la mesure, sa valeur n'a pas de sens
psutil.cpu_percent(interval=None)
last_measure = time.monotonic()
last_core_response = time.monotonic()  # derniere fois que core a repondu



while True:
    time.sleep(INTERVAL)
    now = time.monotonic()
    order: Order = send_heartbeat(get_instance_data(now - last_measure)) # c'est ici que on envoie, et recoit l'ordre de core
    last_measure = now

    if order is not None:
        last_core_response = now
    elif now - last_core_response > CORE_TIMEOUT and worker.limits is not None:
        # core injoignable depuis trop longtemps : plus personne ne surveille la machine, on arrete la tache par securite
        # elle reprendra quand core repondra start
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
            # docker indisponible : on continue, l'ordre sera renvoye au prochain heartbeat
            print(f"Échec de l'ordre {order.action} : {e}", flush=True)