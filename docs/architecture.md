# gleaner : comment le code fonctionne

Ce document décrit le code tel qu'il est aujourd'hui : quel fichier fait quoi, et comment l'agent et core se parlent. Le planning et les choix de conception sont dans [plan.md](plan.md).

## Vue d'ensemble

```
┌──────────────────── core (chez nous) ─────────────────────┐
│ task_manager_api.py   reçoit les heartbeats, répond       │
│ scheduler.py          choisit l'ordre à renvoyer          │
│ instances.py          dernières mesures de chaque EC2     │
│ waste.py              compute gaspillé et son coût        │
└──────────────▲─────────────────────────┬──────────────────┘
               │ 1. heartbeat            │ 2. réponse = ordre
               │    (CPU, mémoire)       │    (start / stop + limites)
┌──────────────┴─────────────────────────▼──────────────────┐
│ EC2 du client                                             │
│   agent.py ──3. docker run / update / kill──▶ worker      │
└───────────────────────────────────────────┬───────────────┘
                                            │ 4. lit / supprime
                                      ┌─────▼─────┐
                                      │ file SQS  │
                                      └───────────┘
```

**À retenir : le heartbeat sert dans les deux sens.** L'agent y envoie ses mesures, et core y répond avec l'ordre que l'agent doit exécuter. Core n'appelle jamais l'agent : c'est toujours l'agent qui ouvre la connexion, donc le client n'a aucun port entrant à ouvrir.

## Le cycle d'un heartbeat

Toutes les `INTERVAL` secondes (5 par défaut) :

| # | Où | Ce qui se passe |
|---|---|---|
| 1 | agent : `get_free_resources()` | Mesure le CPU libre (moyenne sur toute la période), la mémoire libre et le nombre de vCPU. Retourne un `HeartbeatData`. |
| 2 | agent : `send_heartbeat()` | Envoie le `HeartbeatData` en JSON à `POST /heartbeat/{instance_id}`. |
| 3 | core : `heartbeat()` dans `task_manager_api.py` | FastAPI convertit le JSON en `HeartbeatData` (erreur 422 s'il est mal formé). La mesure est rangée dans `instance_data`. |
| 4 | core : `record_waste()` dans `waste.py` | Ajoute le CPU libre de la période au total gaspillé de l'instance. |
| 5 | core : `choose_container_action()` dans `scheduler.py` | Décide si le worker doit tourner sur cette instance, et avec quelles limites. Retourne un `Order`. |
| 6 | core : `heartbeat()` | **Renvoie cet `Order` comme réponse au heartbeat.** |
| 7 | agent : `send_heartbeat()` | Convertit le JSON reçu en `Order` avec `Order.model_validate(...)`. |
| 8 | agent : `start_worker()` / `stop_worker()` | Exécute l'ordre : lance le worker s'il ne tourne pas, le tue s'il doit s'arrêter. |

Si core est injoignable, l'agent affiche l'erreur et réessaie au tour suivant. **Si core ne répond plus depuis `CORE_TIMEOUT` secondes (15 par défaut), l'agent arrête la tâche en cours par sécurité** : plus personne ne surveille la machine. Elle reprendra quand core répondra de nouveau `start`. Si Docker échoue (image introuvable, Docker arrêté), l'agent affiche l'erreur et continue : le même ordre reviendra au heartbeat suivant.

## Les messages échangés

Les deux modèles sont **définis deux fois**, une fois dans l'agent et une fois dans core. Ce sont deux programmes séparés, sur deux machines différentes, qui ne partagent que le JSON. **Si tu modifies un modèle d'un côté, modifie-le aussi de l'autre.**

### `HeartbeatData` : de l'agent vers core

Défini dans `agent/ec2/agent.py` et `core/ec2/task_manager_api.py`.

```json
{"cpu_free_percent": 72.5, "mem_free_mb": 3100, "vcpus": 4, "timestamp": 1759600000, "period_seconds": 5.0}
```

| Champ | Sens |
|---|---|
| `cpu_free_percent` | % du CPU de toute la machine resté libre pendant la période |
| `mem_free_mb` | mémoire disponible, en Mo |
| `vcpus` | nombre de vCPU de la machine |
| `timestamp` | heure de la mesure (secondes Unix) |
| `period_seconds` | durée couverte par la mesure, utilisée pour le calcul du gaspillage |
| `client_cpu_percent` | % du CPU de la machine utilisé par l'application du client, **sans nos tâches**. Calculé par l'agent : CPU utilisé de la machine − CPU de `gleaner-task` (mesuré avec l'API Docker, sur ~1 s), ramené à 0 s'il est négatif |
| `client_mem_free_mb` | mémoire qui serait libre **sans nos tâches** : mémoire disponible de la machine + mémoire de `gleaner-task` (mesurée avec l'API Docker). Calculée par l'agent |

### `Order` : de core vers l'agent

Défini dans `agent/ec2/agent.py` et `core/ec2/scheduler.py`.

```json
{"action": "start", "cpus": 2.0, "memory_mb": 3488}
{"action": "stop", "cpus": null, "memory_mb": null}
```

| `action` | Ce que l'agent doit faire |
|---|---|
| `start` | Le worker doit tourner avec ces limites (`--cpus`, `--memory`). S'il ne tourne pas, le lancer. S'il tourne déjà, ne rien faire. *(Pas encore fait : mettre à jour ses limites si elles ont changé.)* |
| `stop` | Le worker ne doit pas tourner. S'il tourne, le tuer (`kill`, arrêt brutal). |

L'ordre décrit **l'état voulu**, pas un événement : core renvoie le même ordre à chaque heartbeat tant que la situation ne change pas. Si une réponse se perd, la suivante la remplace.

## Les fichiers

### Agent (`agent/ec2/`)

| Fichier | Rôle |
|---|---|
| `agent.py` | Le programme de l'agent : mesure, heartbeat, exécution des ordres. Configuré par les variables d'environnement `CORE_URL`, `INSTANCE_ID`, `INTERVAL` et `CORE_TIMEOUT` (secondes sans réponse de core avant d'arrêter la tâche). |
| `Dockerfile` | Image de l'agent. La commande pour la construire et la lancer est en commentaire en haut du fichier. |
| `requirements.txt` | Dépendances de l'agent uniquement (`docker`, `psutil`, `pydantic`, `requests`). |

L'agent tourne dans un conteneur avec le socket Docker de la machine monté (`/var/run/docker.sock`), pour pouvoir lancer et tuer le worker. Ce socket donne un accès équivalent à root sur la machine.

Le worker est toujours nommé `gleaner-worker` : c'est ce nom qui garantit qu'il n'y en a qu'un par instance, et qui permet à l'agent de le retrouver. Un worker arrêté reste sur la machine (avec ses logs) jusqu'au `start` suivant, qui le supprime avant d'en lancer un nouveau.

### Core (`core/ec2/`)

Core se lance depuis `core/ec2/` : `uvicorn task_manager_api:app`.

| Fichier | Rôle |
|---|---|
| `task_manager_api.py` | L'API FastAPI : les routes, et le modèle `HeartbeatData`. |
| `scheduler.py` | La décision : `choose_container_action()` et le modèle `Order`. Les règles sont dans des constantes en haut du fichier. |
| `instances.py` | `instance_data` : la dernière mesure reçue de chaque instance. Dans un fichier à part pour que l'API et le scheduler puissent l'importer sans import circulaire. |
| `waste.py` | Le compute gaspillé par instance (`record_waste`, `get_waste_report`) et les prix du vCPU·heure (`prices`, saisis à la main). |

Tout est gardé **en mémoire** : un redémarrage de core efface les mesures et le gaspillage.

### Infrastructure (`core/infra/`)

Terraform de la file SQS : `task_queue`, sa dead-letter queue `dlq_task_queue`, et les URL des deux files en sortie. Pas encore déployé.

## Les routes de l'API

| Route | Appelée par | Rôle |
|---|---|---|
| `POST /heartbeat/{instance_id}` | l'agent | Reçoit les mesures, **répond avec l'`Order`** |
| `GET /instances_data` | un humain, pour vérifier | Dernières mesures de toutes les instances |
| `GET /instances_data/{instance_id}` | un humain, pour vérifier | Dernières mesures d'une instance (404 si inconnue) |
| `GET /waste/{instance_id}` | le client | vCPU·heures gaspillées et coût si le prix est connu (404 si inconnue) |

## Règles de décision du scheduler

Constantes en haut de `scheduler.py`, à ajuster après les tests :

Le scheduler décide sur le **CPU du client** seulement, sans compter nos tâches : c'est le champ `client_cpu_percent`, calculé par l'agent. Sinon, une tâche qui consomme du CPU ferait croire que le client est chargé, et core la tuerait en boucle.

- **démarrer** une tâche seulement si le client utilise **moins de 70 %** du CPU et qu'il reste **au moins 1 Go** de mémoire libre sans compter nos tâches (`client_mem_free_mb`) ;
- **arrêter** une tâche en cours seulement si le client dépasse **90 %** du CPU (ou si la mémoire libre passe sous 1 Go) ;
- **entre 70 et 90 %**, rien ne change : une tâche en cours continue, sinon on n'en lance pas. Pour ça, core garde le dernier ordre envoyé à chaque instance (`last_orders`) ;
- **30 % du CPU et 512 Mo** de mémoire restent à l'application du client au démarrage d'une tâche : `cpus = vCPU × (100 − CPU du client − 30) / 100` ; sous **0,1 vCPU**, pas de tâche ;
- `memory_mb = client_mem_free_mb − 512`.

## Protection de l'application du client par le noyau

En plus des limites (`--cpus`, `--memory`), chaque tâche est lancée avec une **priorité minimale** (dans `worker.py`). Cette protection agit tout de suite, sans attendre un heartbeat ni un `stop` de core, et même si l'agent ou core sont en panne :

- **`cpu_shares=2`** (1024 par défaut) : quand l'application du client veut du CPU, le noyau le lui donne en priorité. Mesuré sur un même cœur : 99 % pour l'application, 1 % pour la tâche. Quand le CPU est libre, la tâche utilise tout ce que `--cpus` lui autorise.
- **`oom_score_adj=1000`** : si la machine manque de mémoire, le noyau tue la tâche en premier, jamais l'application du client. Son message revient dans la file.

Limite : le partage par poids se fait entre cgroups de même niveau. C'est efficace si l'application du client tourne comme service systemd ou dans un conteneur Docker. Si elle est lancée à la main depuis une session SSH, la protection n'est que partielle ; à traiter à l'installation sur EC2 (cgroup dédié de faible poids pour les tâches gleaner).

## Qui fait quoi

| | Rôle | Ne fait pas |
|---|---|---|
| **core** | Décide **si** le worker tourne sur une instance, et avec quelles limites. | Ne touche jamais aux messages SQS. Ne se connecte jamais à la machine du client. |
| **agent** | Mesure, envoie le heartbeat, exécute l'ordre reçu. | Ne lit pas SQS, ne traite aucune unité. |
| **worker** (conteneur) | Lit **un** message SQS, traite l'unité, supprime le message, recommence. | Ne décide pas quand il tourne. |

Le worker contient la boucle SQS de gleaner. Le traitement d'une unité vient de l'ingénieur, sous forme d'une image Docker et d'une commande, qui reçoit le message dans la variable d'environnement `GLEANER_UNIT` (voir « Comment l'ingénieur fournit son traitement » dans [plan.md](plan.md)). C'est le worker, et pas core, qui lit SQS : le message doit être supprimé par celui qui a traité l'unité, et seulement après le succès. Si le worker est tué en plein milieu, rien n'est supprimé et SQS remet le message en file tout seul. *(Le worker n'est pas encore écrit.)*

## Pas encore fait

- **Mettre à jour les limites du worker** quand il tourne déjà et que core renvoie un `start` avec d'autres valeurs (`docker update`).
- **Le worker** (image et boucle SQS) et le rôle IAM qui lui donne accès à SQS.
