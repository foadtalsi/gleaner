# core API: receives heartbeats from agents and replies with the order to execute
# full walkthrough: docs/architecture.md
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from llm import choose_task_iam_role, create_task_code, send_task_to_sqs
from instances import instance_data
from scheduler import choose_container_action
from waste import get_waste_report, record_waste
from agent_key import AGENTS_KEY_DIR
from users import task_possible_roles, users


app = FastAPI()
tasks = []  # tasks received from the web interface, which will be put in the SQS queue for the agents to pick up

# message sent by the agent, must stay identical to HeartbeatData in agent/ec2/agent.py
class HeartbeatData(BaseModel):
    cpu_free_percent: float
    vcpus: int
    period_seconds: float  # duration covered by the measurement
    client_cpu_percent: float  # % of the machine's cpu used by the client's application (without our tasks), computed by the agent
    client_mem_free_mb: int  # free memory if our tasks were not there, computed by the agent
    agent_key: str  # so core knows the heartbeat really comes from the agent, and not from an intruder


class Task(BaseModel):
    task_id: str
    description: str
    input_resources: list[str]  # resources the task can only read, split into batches by core, e.g. "s3://bucket/photos/"
    output_resources: list[str]  # resources the task can only write to, never split, e.g. "s3://bucket/resized/"
    user_name: str  # name of the person creating the task, see users.py
    user_key: str  # their key, to prove it is really them


# what the admin sends to change a person's roles
class AllowedRolesUpdate(BaseModel):
    admin_key: str  # key of the admin making the request
    allowed_roles: list[str]  # the person's new list of roles, taken from task_possible_roles

class TaskValidation(BaseModel):
    validator_name: str # person who will validate the task, the reviewer
    validator_key: str # validator's key, to prove it is really them


# the agent sends its measurements, core stores them, counts the waste, and replies with an Order (start / stop)
@app.post("/heartbeat/{instance_id}")
def heartbeat(instance_id: str, data: HeartbeatData):
    # Check that the agent key is valid
    if data.agent_key not in AGENTS_KEY_DIR.values():
        raise HTTPException(status_code=403, detail="Clé d'agent non valide")

    instance_data[instance_id] = data

    record_waste(instance_id, data.cpu_free_percent, data.vcpus, data.period_seconds)

    return choose_container_action(instance_id, data)  # the agent receives its orders in the heartbeat response
 



@app.get("/instances_data") # returns the data, e.g. cpu, of all ec2 instances
def get_instances_data():
    return {"instances_data": instance_data}


@app.get("/instances_data/{instance_id}") # returns the data of a single instance, e.g. cpu of one ec2 instance
def get_instance_data(instance_id: str):
    if instance_id in instance_data:
        return {"instance_data": instance_data[instance_id]}
    else:
        raise HTTPException(status_code=404, detail="Instance not found")


@app.get("/waste/{instance_id}") # returns the compute wasted by an ec2 instance, and its cost if the price is known
def get_waste(instance_id: str):
    report = get_waste_report(instance_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return report


@app.post("/task") # creates a task from the web interface, with the iam role and the code; the task waits for validation by a reviewer, who will put it in the SQS queue for the agents to pick up
def create_task(task: Task):
    # 1. the person must exist
    if task.user_name not in users:
        raise HTTPException(status_code=403, detail="Utilisateur inconnu")

    # 2. the key must be theirs, otherwise anyone could create a task on their behalf
    if users[task.user_name]["key"] != task.user_key:
        raise HTTPException(status_code=403, detail="Clé d'utilisateur non valide")

    # 3. choose the IAM role the task must use, based on what the person is allowed to give to their tasks
    task_iam_role = choose_task_iam_role(task)

    # 4. create the task code with the LLM
    task_code = create_task_code(task)

    # 5. the task waits for another person's review: nothing goes to SQS here

    tasks.append({"task": task, "code": task_code, "role": task_iam_role, "status": "a_valider"})

    return {"task_id": task.task_id, "code": task_code, "role": task_iam_role, "status": "a_valider"}


@app.get("/tasks") # returns the list of tasks received from the web interface
def get_tasks():
    # the author's key is left out: anyone reading it could create or validate tasks in their name
    tasks_without_keys = []
    for saved_task in tasks:
        tasks_without_keys.append({
            "task_id": saved_task["task"].task_id,
            "description": saved_task["task"].description,
            "input_resources": saved_task["task"].input_resources,
            "output_resources": saved_task["task"].output_resources,
            "user_name": saved_task["task"].user_name,
            "code": saved_task["code"],
            "role": saved_task["role"],
            "status": saved_task["status"],
        })
    return {"tasks": tasks_without_keys}


@app.get("/", response_class=HTMLResponse) # the web interface, a single page
def get_web_interface():
    with open("index.html") as file:
        return file.read()


@app.get("/users") # people and the roles they can give to their tasks, without their keys, for the admin page
def get_users():
    users_without_keys = {}
    for name, user in users.items():
        users_without_keys[name] = {"is_admin": user["is_admin"], "allowed_roles": user["allowed_roles"]}
    return {"users": users_without_keys}


@app.get("/roles") # the client's closed list of roles, with the services each one gives access to
def get_roles():
    return {"roles": task_possible_roles}


@app.put("/users/{name}/allowed_roles") # an admin changes the IAM roles that the person "name" is allowed to give to their tasks
def set_allowed_roles(name: str, admin_request: AllowedRolesUpdate):
    # 1. the request must come from an admin
    sender_is_admin = False
    for user in users.values():
        if user["key"] == admin_request.admin_key:
            if user["is_admin"]:
                sender_is_admin = True
    if sender_is_admin == False:
        raise HTTPException(status_code=403, detail="Réservé aux admins")

    # 2. the person to modify must exist
    if name not in users:
        raise HTTPException(status_code=404, detail="Personne inconnue")

    # 3. each role must be in the closed list
    for role in admin_request.allowed_roles:
        if role not in task_possible_roles:
            raise HTTPException(status_code=400, detail="Rôle inconnu : " + role)

    # 4. all good: replace the person's list
    users[name]["allowed_roles"] = admin_request.allowed_roles
    return {"name": name, "allowed_roles": users[name]["allowed_roles"]}


@app.post("/tasks/{task_id}/validate") # lets a reviewer validate a task created by another person
def validate_task(task_validation: TaskValidation, task_id: str):

    task_to_validate = None # we only know the task id, not the task itself, so we can't check the iam role yet

    # 1. find the task stored by core

    for task in tasks:
        if task["task"].task_id == task_id:
            task_to_validate = task

    if task_to_validate is None:
        raise HTTPException(status_code=404, detail="Tâche inconnue")


    author_name = task_to_validate["task"].user_name
    task_role = task_to_validate["role"]
    validator = task_validation.validator_name

    # 2. the validator must exist
    if validator not in users:
        raise HTTPException(status_code=403, detail="Validateur inconnu")

    # 3. the key must be theirs
    if users[validator]["key"] != task_validation.validator_key:
        raise HTTPException(status_code=403, detail="Clé du validateur non valide")

    # 4. the validator cannot be the author: four-eyes rule
    if validator == author_name:
        raise HTTPException(status_code=403, detail="L'auteur ne peut pas valider sa propre tâche")

    # 5. the task must not already be validated, otherwise it would go to SQS twice
    if task_to_validate["status"] != "a_valider":
        raise HTTPException(status_code=400, detail="Tâche déjà validée")

    # 6. the task's role must be in the author's allowed roles
    if task_role not in users[author_name]["allowed_roles"]:
        raise HTTPException(status_code=403, detail="Rôle non autorisé pour l'auteur")


    # 7. put the task in the sqs queue, then mark it validated

    send_task_to_sqs(task=task_to_validate["task"], task_code=task_to_validate["code"], task_iam_role=task_to_validate["role"])
    task_to_validate["status"] = "validee"

    return {"task_id": task_to_validate["task"].task_id, "code": task_to_validate["code"], "role": task_to_validate["role"], "status": "validee"}

    
    
