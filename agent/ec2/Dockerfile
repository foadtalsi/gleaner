# Image de l'agent gleaner
#
# Construire :
#   docker build -t gleaner-agent agent/ec2
#
# Lancer :
#   docker run -d --name gleaner-agent --restart unless-stopped \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -e CORE_URL=http://host.docker.internal:8000 \
#     -e INSTANCE_ID=local-dev \
#     -e QUEUE_URL=<url de la file SQS> \
#     -e TASK_IMAGE=<image de l'ingenieur> \
#     -e TASK_COMMAND="<commande>" \
#     -e AWS_REGION=eu-west-3 \
#     gleaner-agent
#
# Le socket Docker est monte pour que le worker puisse lancer et tuer les conteneurs de l'ingenieur.
# Attention : il donne a l'agent un acces equivalent a root sur la machine.
# Sur Mac, host.docker.internal pointe vers la machine qui fait tourner Docker ; sur une EC2, mettre la vraie URL de core.

FROM python:3.12-slim

WORKDIR /app

# dependances d'abord, pour ne pas les reinstaller a chaque modif du code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py worker.py ./

# valeurs par defaut, a surcharger au lancement avec -e
ENV CORE_URL=http://localhost:8000 \
    INSTANCE_ID=local-dev \
    INTERVAL=5

CMD ["python", "agent.py"]
