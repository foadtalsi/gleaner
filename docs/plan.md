# gleaner : architecture et plan des premières semaines

Stack : **Python**. Les tâches sont exécutées dans des **conteneurs Docker**.

## MVP

### 1. Le producteur 📤

C'est **l'ingénieur lui-même**, pas gleaner, qui :
- découpe sa tâche en petits morceaux indépendants ;
- envoie chaque morceau dans SQS.

### 2. Le worker sur l'EC2 🔁 (une boucle)

- Lit le CPU libre en local (`psutil`).
- Si assez de marge → prend un message dans SQS.
- Exécute le morceau.
- Si ça réussit → supprime le message ✅
- Si ça plante → ne fait rien, SQS le remet dans la file.
- Si pas assez de marge → attend un peu et recommence.

### 3. Enregistrement du CPU gaspillé 📉

Le CPU qui reste inutilisé, **même avec les tâches qui tournent**, est enregistré. On sait ainsi combien de compute est gaspillé, et on peut réduire les coûts.

C'est le cas, par exemple :
- quand la file SQS est vide ;
- quand un morceau n'utilise qu'une partie du CPU libre, par exemple un traitement mono-thread sur une machine à 4 vCPU.

**Mesure.** La mesure tourne dans un **thread séparé** de la boucle principale. Sinon, pendant l'exécution d'un morceau, la boucle est bloquée et ne mesure rien, alors que c'est justement à ce moment-là qu'il faut savoir si du CPU reste inutilisé.

Toutes les `INTERVALLE` secondes, le thread :
1. mesure le CPU inutilisé de toute la machine (application, tâches et reste) ;
2. le convertit en **vCPU·secondes gaspillées** : `% inutilisé × nb de vCPU × INTERVALLE` ;
3. ajoute une ligne dans `gaspillage.csv` : `horodatage, % CPU inutilisé, vCPU·s gaspillées`.

La somme des vCPU·secondes, divisée par 3600, donne des vCPU·heures. On les multiplie par le prix d'un vCPU·heure de l'instance pour obtenir le coût.




**À décider**
- **La marge compte-t-elle comme gaspillée ?** Le CPU laissé volontairement libre pour l'application (`MARGE_CPU`) est inutilisé, mais ce n'est pas une erreur. Faut-il l'enregistrer dans le gaspillage, ou seulement le CPU inutilisé au-delà de la marge ?
- **Où enregistrer ?** Le fichier local `gaspillage.csv` suffit pour le MVP. Si tu veux voir le total de plusieurs EC2 au même endroit, il faudra envoyer ces chiffres ailleurs.

## Architecture

```
┌──────────────── core ────────────────┐
│ ec2/                                 │
│   collector.py     task_manager.py   │
│ infra/                               │
└──────────────────▲───────────────────┘
                   │ ressources libres
┌──────────────────┴───────────────────┐
│ agent (EC2 / ECS)                    │
└──────────────────────────────────────┘
```

Core est découpé par service : un dossier par service (`ec2/`, puis `ecs/`).

- `collector.py` reçoit les données envoyées par l'agent qui tourne chez le client.
- `task_manager.py` gère les tâches en cours sur les machines de ce service.

`infra/` contient l'infrastructure Terraform.

### Agent

- Mesure le CPU et la mémoire inutilisés de la machine (instance EC2 ou service ECS).
- Envoie ces informations à core à intervalle régulier.

### Core

- Reçoit les ressources libres envoyées par les agents.
- Décide quelles tâches lancer et sur quelle machine, selon les ressources libres.
- Lance chaque tâche dans un conteneur Docker, limité en CPU et mémoire (`--cpus`, `--memory`) aux ressources libres, pour ne pas gêner les applications en place.
- Arrête une tâche si les ressources libres de sa machine ne suffisent plus.

## Tâches découpées et file SQS

### Le besoin

Les tâches tournent avec la puissance récupérée. Dès que le CPU de l'EC2 augmente, elles doivent pouvoir **s'arrêter d'un coup**, sans prévenir et sans rien casser.

Pour cela :

- Une tâche est **découpée en petites unités indépendantes**. Chaque unité peut être traitée seule, sur n'importe quelle machine, dans n'importe quel ordre.
- Les unités sont placées dans une **file SQS**.
- Si une unité est coupée en plein milieu, on ne perd que cette unité, et elle est refaite plus tard.

### Fonctionnement

```
  tâche ──découpage──▶ unités ──▶ ┌───────────┐
                                  │ file SQS  │
                                  └─────┬─────┘
                                        │ 1 unité à la fois
                       ┌────────────────▼────────────────┐
                       │ EC2 du client                   │
                       │   conteneur Docker (worker)     │
                       │   lit → traite → supprime       │
                       └─────────────────────────────────┘
        CPU de l'EC2 monte ──▶ core ──▶ conteneur tué (docker kill)
```

1. **Découpage.** L'ingénieur découpe lui-même sa tâche en unités, gleaner ne le fait pas. Chaque unité devient un message SQS qui décrit ce qu'il y a à traiter, par exemple un fichier ou une plage de données.
2. **Traitement.** Sur l'EC2, le conteneur Docker lit **un seul message à la fois**. Il traite l'unité, puis **supprime le message** de la file, et seulement une fois l'unité terminée.
3. **Arrêt brutal.** Quand l'agent signale que le CPU de l'EC2 monte, core fait tuer le conteneur immédiatement. Il n'attend pas la fin de l'unité en cours.
4. **Reprise automatique.** Le message de l'unité interrompue n'a pas été supprimé. SQS le rend de nouveau visible à la fin du *visibility timeout*, et un autre conteneur le reprend quand de la puissance se libère.

C'est le *visibility timeout* de SQS qui rend l'arrêt brutal sans danger. Un message lu devient invisible pendant un temps donné. S'il n'est pas supprimé pendant ce temps, il revient dans la file. core n'a donc rien à faire pour remettre une unité interrompue en file.



### Règles pour les unités

- **Courtes.** Une unité coupée est refaite depuis le début. Plus elle est courte, moins on perd de travail, par exemple quelques minutes au plus.
- **Indépendantes.** Une unité ne dépend ni du résultat d'une autre, ni de l'ordre de traitement.
- **Rejouables sans effet de bord.** Une même unité peut être traitée deux fois : une fois après un arrêt brutal, ou parce que SQS standard peut livrer un message en double. Son résultat doit donc toujours être écrit au même endroit, par exemple la même clé S3, pour que le refaire donne le même résultat.
- **Visibility timeout plus long que la durée d'une unité.** Sinon, une unité encore en cours réapparaît dans la file et part en double sur une autre machine.

### Points à décider

- **Une file par tâche, ou une file commune ?**
- **Dead-letter queue.** Si on en ajoute une, chaque arrêt brutal compte comme une réception. Un `maxReceiveCount` trop bas enverrait à la poubelle des unités qui ont seulement été interrompues.

La file SQS est créée avec Terraform, dans `infra/`.

## Plan

| Semaine | Contenu |
|---|---|
| 1 | Agent : mesure du CPU et de la mémoire libres, envoi à core. Core : réception de ces informations. |
| 2 | File SQS en Terraform, dans laquelle l'ingénieur envoie les unités qu'il a découpées. |
| 3 | Lancement sur l'EC2 d'un conteneur Docker qui lit SQS unité par unité, avec les limites CPU et mémoire. |
| 4 | Arrêt brutal du conteneur quand le CPU de l'EC2 monte. Vérification que les unités interrompues reviennent dans SQS et sont refaites. |
| 5 | Installation de l'agent sur une instance EC2 et sur un service ECS. |


