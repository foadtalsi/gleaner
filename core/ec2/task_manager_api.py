# API de core : recoit les heartbeats des agents et leur repond avec l'ordre a executer
# fonctionnement complet : docs/architecture.md
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from instances import instance_data
from scheduler import choose_container_action
from waste import get_waste_report, record_waste
from agent_key import AGENTS_KEY_DIR

app = FastAPI()
tasks = []  # liste des taches recues de l'interface web, qui seront mises dans la file SQS pour que les agents les recupere

# message envoye par l'agent, doit rester identique au HeartbeatData de agent/ec2/agent.py
class HeartbeatData(BaseModel):
    cpu_free_percent: float
    vcpus: int
    period_seconds: float  # durée couverte par la mesure
    client_cpu_percent: float  # % du cpu de la machine utilise par l'application du client (sans nos taches), calcule par l'agent
    client_mem_free_mb: int  # memoire libre si nos taches n'etaient pas la, calculee par l'agent
    agent_key: str  # pour que core sache que c'est bien l'agent qui envoie le heartbeat, et pas un intrus


class Task(BaseModel):
    task_id: str
    description: str
    resources: list[str]  # ressources que la tache peut utiliser, donnees par l'ingenieur, ex : "s3://bucket/dossier/"
    client_key: str # pour que core sache que c'est bien l'interface web qui envoie la task


# l'agent envoie ses mesures, core les garde, compte le gaspillage, et repond avec un Order (start / stop)
@app.post("/heartbeat/{instance_id}")
def heartbeat(instance_id: str, data: HeartbeatData):
    # Vérifier si la clé de l'agent est valide
    if data.agent_key not in AGENTS_KEY_DIR.values():
        raise HTTPException(status_code=403, detail="Clé d'agent non valide")

    instance_data[instance_id] = data

    record_waste(instance_id, data.cpu_free_percent, data.vcpus, data.period_seconds)

    return choose_container_action(instance_id, data)  # l'agent recoit ses ordres dans la reponse au heartbeat
 



@app.get("/instances_data") # sert a recuperer la data ex : cpu de toutes les instances ec2,
def get_instances_data():
    return {"instances_data": instance_data}


@app.get("/instances_data/{instance_id}") # sert a recuperer la data de 1 seule instance ex : cpu d'une instance ec2,
def get_instance_data(instance_id: str):
    if instance_id in instance_data:
        return {"instance_data": instance_data[instance_id]}
    else:
        raise HTTPException(status_code=404, detail="Instance not found")


@app.get("/waste/{instance_id}") # sert a recuperer le compute gaspille d'une instance ec2, et son cout si le prix est connu
def get_waste(instance_id: str):
    report = get_waste_report(instance_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return report


@app.post("/task") # sert a envoyer une tache a core, qui la mettra dans la file SQS pour que les agents la recupere
def create_task(task: Task):
    # Vérifier si la clé du client est valide
    if task.client_key not in AGENTS_KEY_DIR.values():
        raise HTTPException(status_code=403, detail="Clé de client non valide")


    tasks.append(task)
    return {"message": "Task received", "task_id": task.task_id}


@app.get("/tasks") # sert a recuperer la liste des taches recues de l'interface web
def get_tasks():
    return {"tasks": tasks}