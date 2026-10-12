# Worker gleaner : lit les taches dans SQS et lance l'image de l'ingenieur pour chacune
# l'agent le pilote avec start() / stop() selon les ordres de core
import os
import time

import boto3
import docker


QUEUE_URL = os.environ["QUEUE_URL"]
TASK_IMAGE = os.environ["TASK_IMAGE"]  # image de l'ingenieur
TASK_COMMAND = os.environ.get("TASK_COMMAND")  # None : on garde le CMD de l'image
TASK_NAME = "gleaner-task"  # nom fixe, pour que l'agent retrouve le conteneur

sqs = boto3.client("sqs")
docker_client = docker.from_env()


class Worker:
    def __init__(self):
        self.limits = None  # (cpus, memory_mb) recus de core, None = en pause

    def start(self, cpus, memory_mb):
        # les nouvelles limites s'appliquent a partir de la tache suivante
        self.limits = (cpus, memory_mb)

    def stop(self):
        self.limits = None
        try:
            docker_client.containers.get(TASK_NAME).kill()  # arret brutal, le message reviendra dans SQS
        except docker.errors.APIError:
            pass  # aucune tache en cours

    def run_forever(self):
        try:
            # tache laissee par un agent precedent (mise a jour, plantage...) : sinon son nom bloque toutes les taches suivantes
            docker_client.containers.get(TASK_NAME).remove(force=True)
            print("Tâche orpheline supprimée, son message reviendra dans la file", flush=True)
        except docker.errors.NotFound:
            pass

        while True:
            if self.limits is None: # regarde si limite a une valeur, si y'en a sa veut dire que agent.py a recu start() de core, donc on peut commencer, mais si c'est None alors ya rien et on attend 1 seconde
                time.sleep(1)  # en pause, on attend un start
                continue
            try:
                response = sqs.receive_message(QueueUrl=QUEUE_URL, WaitTimeSeconds=10)
                for message in response.get("Messages", []):
                    current = self.limits
                    if current is None:
                        break  # stop recu pendant l'attente, le message reviendra dans la file
                    cpus, memory_mb = current
                    try:
                        # bloque jusqu'a la fin, leve ContainerError si le code de sortie n'est pas 0
                        docker_client.containers.run(
                            TASK_IMAGE,
                            TASK_COMMAND,
                            name=TASK_NAME,
                            remove=True,
                            environment={"GLEANER_TASK": message["Body"]},
                            nano_cpus=int(cpus * 1e9),  # equivalent de --cpus
                            mem_limit=f"{memory_mb}m",  # equivalent de --memory
                            cpu_shares=2,  # priorite cpu minimale (1024 par defaut) : sous contention, le noyau donne le cpu a l'application du client
                            oom_score_adj=1000,  # si la memoire manque, le noyau tue la tache en premier, jamais l'application du client
                        )
                        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])
                        print("Tâche terminée", flush=True)
                    except docker.errors.ContainerError:
                        # echouee ou tuee : on garde le message, SQS le redonnera apres le visibility timeout
                        print("Tâche interrompue, elle sera refaite", flush=True)
            except Exception as e:
                # SQS injoignable, image introuvable... : le thread ne doit jamais mourir
                print(f"Erreur du worker : {e}", flush=True)
                time.sleep(5)
    

