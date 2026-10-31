# calls to Meta's API: task splitting and IAM role choice
import json
import os
from pydantic import BaseModel
import requests
import boto3
from users import task_possible_roles, users

sqs = boto3.client("sqs")
s3 = boto3.client("s3")

LOT_SIZE = 20  # files per SQS message, later chosen by the LLM
MAX_OUTPUT_TOKENS = 16000  # the model's reasoning counts toward it: too low, nothing is left for the answer


def ask_llm(prompt):
    response = requests.post(
        "https://api.meta.ai/v1/responses",
        headers={"Authorization": f"Bearer {os.environ['meta_api_key']}"},
        json={"model": "muse-spark-1.3", "input": prompt, "max_output_tokens": MAX_OUTPUT_TOKENS},
    )
    response.raise_for_status()
    return response.json()["output"][-1]["content"][0]["text"]  # output[0] is the model's reasoning, the message is the last one


def create_task_code(task):
    task_code = ask_llm(f"Tu es un ingenieur logiciel. Ecris le code pour executer la tache suivante : {task.description}. Le code doit etre en Python, le code doit faire en sorte que la tache doit pouvoir etre arreter a n'importe quelle moment, puis repartir sans probleme, le code doit etre complet, avec toutes les fonctions necessaires. Il ne peut utiliser que la bibliotheque standard de Python, boto3 et Pillow. Il ne peut acceder qu'a ces ressources : {task.resources}. Le code ne traite qu'une partie de la tache : il lit dans la variable d'environnement GLEANER_TASK un JSON de la forme {{\"keys\": [\"s3://bucket/photo1.jpg\", \"s3://bucket/photo2.jpg\"]}}, et ne traite que ces fichiers. Il ne liste pas les ressources lui-meme. Si keys est vide, la tache n'a pas de fichiers et le code fait toute la tache. Il doit etre concis, mais clair. Il doit etre robuste, avec gestion des erreurs. Il doit etre optimisé pour la performance et la memoire. Il doit etre compatible avec Python 3.9+. Le code doit etre renvoye sous forme de texte brut, sans explications ni commentaires, le texte que tu vas renvoyer doit etre executable directement dans un editeur de code.")

    return task_code




def choose_task_iam_role(task):


    prompt = f"Tu es un ingenieur logiciel. Tu dois choisir le role IAM que la tache suivante doit utiliser : {task.description}. La personne qui a cree la tache a le droit de donner a ses taches les roles suivants : {users[task.user_name]['allowed_roles']}.Les roles possibles sont : {task_possible_roles} Le role choisi doit etre pris dans allowed_roles, et doit permettre a la tache d'acceder aux ressources qu'elle utilise. Renvoie uniquement le nom du role choisi, sans explications ni commentaires."

    task_role = ask_llm(prompt).strip()

    if task_role not in users[task.user_name]["allowed_roles"]: # checks that the role chosen by the LLM is in the list of roles the person can give to their tasks
        raise ValueError(f"Role non autorise : {task_role}")

    if task_role not in task_possible_roles: # checks that the role chosen by the LLM is in the list of possible roles
        raise ValueError(f"Role inconnu : {task_role}")



    return task_role






def send_task_to_sqs(task, task_code, task_iam_role):
    # lists the files of the s3:// resources, splits them into batches, and sends one SQS message per batch with the code
    files = []
    for resource in task.resources:
        if resource.startswith("s3://"):
            bucket, _, prefix = resource[len("s3://"):].partition("/")  # "s3://bucket/folder/" -> "bucket", "folder/"
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)  # 1000 files at most
            for s3_object in response.get("Contents", []):
                files.append(f"s3://{bucket}/{s3_object['Key']}")

    lots = []
    for start in range(0, len(files), LOT_SIZE):
        lots.append(files[start:start + LOT_SIZE])
    if len(lots) == 0:
        lots.append([])  # task without files: a single message, the code does the whole task

    for lot in lots:
        message = {"resources": task.resources, "keys": lot, "code": task_code, "iam_role": task_possible_roles[task_iam_role]["arn"]}  # the ARN: it's what the worker gives to assume_role
        sqs.send_message(QueueUrl=os.environ["QUEUE_URL"], MessageBody=json.dumps(message))

    return len(lots)





