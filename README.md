# gleaner

**gleaner** est un logiciel qui repère les ressources inutilisées dans une infrastructure cloud et s'en sert pour exécuter des tâches.

Exemple : une instance EC2 dont 70 % du CPU reste inutilisé. gleaner récupère cette puissance de calcul libre pour y faire tourner des tâches, au lieu de la laisser perdue.


## Architecture

- **core** : le cœur du logiciel. Il reçoit les informations des agents, décide quelles tâches lancer et où, et utilise les ressources libres sans gêner les applications déjà en place.
- **agent** : un petit programme installé sur l'infrastructure de l'utilisateur. Il mesure les ressources disponibles (par exemple le CPU inutilisé d'une instance EC2 ou d'un service ECS) et envoie ces informations à core.
