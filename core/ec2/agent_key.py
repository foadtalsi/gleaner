# gere les key des agents ec2 pour que seule core puisse envoyer des commandes aux agents ec2
# Plus tard mettre la key dans dynamodb pas en dur dans le code

agent1_key = "agent_1_key"

AGENTS_KEY_DIR = {"agent_1": agent1_key} # liste de key connu de core, il check si ce que le agent envoie a core est dans sa liste
