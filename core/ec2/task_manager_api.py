# API de core : recoit les heartbeats des agents et leur repond avec l'ordre a executer
# fonctionnement complet : docs/architecture.md
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from llm import choose_task_iam_role, create_task_code
from instances import instance_data
from scheduler import choose_container_action
from waste import get_waste_report, record_waste
from agent_key import AGENTS_KEY_DIR
from users import task_possible_roles, users

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
    user_name: str  # nom de la personne qui cree la tache, voir users.py
    user_key: str  # sa cle, pour prouver que c'est bien elle


# ce que l'admin envoie pour changer les roles d'une personne
class AllowedRolesUpdate(BaseModel):
    admin_key: str  # cle de l'admin qui fait la demande
    allowed_roles: list[str]  # la nouvelle liste de roles de la personne, pris dans task_possible_roles


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
    # 1. la personne doit exister
    if task.user_name not in users:
        raise HTTPException(status_code=403, detail="Utilisateur inconnu")

    # 2. la cle doit etre la sienne, sinon n'importe qui pourrait creer une tache a sa place
    if users[task.user_name]["key"] != task.user_key:
        raise HTTPException(status_code=403, detail="Clé d'utilisateur non valide")

    # 3. on choisit le role IAM que la tache doit utiliser, en fonction de ce que la personne a le droit de donner a ses taches
    task_iam_role = choose_task_iam_role(task)

    # 4. on creer le code de la tache avec le LLM
    task_code = create_task_code(task)

    # 5. la tache attend la revue d'une autre personne : rien ne part dans SQS ici
    tasks.append({"task": task, "code": task_code, "role": task_iam_role, "status": "a_valider"})
    return {"task_id": task.task_id, "code": task_code, "role": task_iam_role, "status": "a_valider"}


@app.get("/tasks") # sert a recuperer la liste des taches recues de l'interface web
def get_tasks():
    return {"tasks": tasks}

@app.put("/users/{name}/allowed_roles") # un admin change les roles IAM que la personne "name" a le droit de donner a ses taches
def set_allowed_roles(name: str, admin_request: AllowedRolesUpdate):
    # 1. la demande doit venir d'un admin
    sender_is_admin = False
    for user in users.values():
        if user["key"] == admin_request.admin_key:
            if user["is_admin"]:
                sender_is_admin = True
    if sender_is_admin == False:
        raise HTTPException(status_code=403, detail="Réservé aux admins")

    # 2. la personne a modifier doit exister
    if name not in users:
        raise HTTPException(status_code=404, detail="Personne inconnue")

    # 3. chaque role doit etre dans la liste fermee
    for role in admin_request.allowed_roles:
        if role not in task_possible_roles:
            raise HTTPException(status_code=400, detail="Rôle inconnu : " + role)

    # 4. tout est bon : on remplace la liste de la personne
    users[name]["allowed_roles"] = admin_request.allowed_roles
    return {"name": name, "allowed_roles": users[name]["allowed_roles"]}
