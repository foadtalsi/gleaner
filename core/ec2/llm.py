# appels a l'API de Meta : decoupage des taches et choix du role IAM
import json
import os
from pydantic import BaseModel
from task_manager_api import tasks
from task_manager_api import Task
import requests
import boto3

sqs = boto3.client("sqs")
s3 = boto3.client("s3")

LOT_SIZE = 20  # fichiers par message SQS, plus tard choisi par le LLM


def ask_llm(prompt):
    response = requests.post(
        "https://api.meta.ai/v1/responses",
        headers={"Authorization": f"Bearer {os.environ['meta_api_key']}"},
        json={"model": "muse-spark-1.3", "input": prompt},
    )
    response.raise_for_status()
    return response.json()["output"][-1]["content"][0]["text"]  # output[0] est la reflexion du modele, le message est le dernier


def create_task_code(task : Task):
    task_code = ask_llm(f"Tu es un ingenieur logiciel. Ecris le code pour executer la tache suivante : {task.description}. Le code doit etre en Python, le code doit faire en sorte que la tache doit pouvoir etre arreter a n'importe quelle moment, puis repartir sans probleme, le code doit etre complet, avec toutes les fonctions necessaires. Il ne peut utiliser que la bibliotheque standard de Python, boto3 et Pillow. Il ne peut acceder qu'a ces ressources : {task.resources}. Le code ne traite qu'une partie de la tache : il lit dans la variable d'environnement GLEANER_TASK un JSON de la forme {{\"keys\": [\"s3://bucket/photo1.jpg\", \"s3://bucket/photo2.jpg\"]}}, et ne traite que ces fichiers. Il ne liste pas les ressources lui-meme. Si keys est vide, la tache n'a pas de fichiers et le code fait toute la tache. Il doit etre concis, mais clair. Il doit etre robuste, avec gestion des erreurs. Il doit etre optimisé pour la performance et la memoire. Il doit etre compatible avec Python 3.9+. Le code doit etre renvoye sous forme de texte brut, sans explications ni commentaires, le texte que tu vas renvoyer doit etre executable directement dans un editeur de code.")

    return task_code


def send_task_to_sqs(task: Task, task_code):
    # liste les fichiers des ressources s3://, les coupe en lots, et envoie un message SQS par lot avec le code
    files = []
    for resource in task.resources:
        if resource.startswith("s3://"):
            bucket, _, prefix = resource[len("s3://"):].partition("/")  # "s3://bucket/dossier/" -> "bucket", "dossier/"
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)  # 1000 fichiers au plus
            for s3_object in response.get("Contents", []):
                files.append(f"s3://{bucket}/{s3_object['Key']}")

    lots = []
    for start in range(0, len(files), LOT_SIZE):
        lots.append(files[start:start + LOT_SIZE])
    if len(lots) == 0:
        lots.append([])  # tache sans fichiers : un seul message, le code fait toute la tache

    for lot in lots:
        message = {"resources": task.resources, "keys": lot, "code": task_code}
        sqs.send_message(QueueUrl=os.environ["QUEUE_URL"], MessageBody=json.dumps(message))

    return len(lots)





