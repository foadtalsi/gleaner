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

**Mesure.** L'agent mesure le CPU inutilisé de toute la machine (application, tâches et reste), en moyenne sur toute la période depuis la mesure précédente. Il l'envoie à core avec le nombre de vCPU et la durée de la période, dans son heartbeat.

Core convertit chaque heartbeat en **vCPU·secondes gaspillées** : `% inutilisé × nb de vCPU × durée de la période`, et les ajoute au total de l'instance.

La somme des vCPU·secondes, divisée par 3600, donne des vCPU·heures. On les multiplie par le prix d'un vCPU·heure de l'instance pour obtenir le coût.

**Décisions**
- **Tout le CPU inutilisé compte comme gaspillé**, y compris la marge laissée à l'application, sans distinction.
- **Le total est gardé en mémoire** dans core (`core/ec2/waste.py`). Il est remis à zéro quand core redémarre.
- **Le prix du vCPU·heure est saisi à la main** par instance, dans le dictionnaire `prices` de `core/ec2/waste.py`. Plus tard, il viendra de l'API Pricing d'AWS.
- **Le client consulte son gaspillage** via `GET /waste/{instance_id}` : vCPU·heures gaspillées et coût si le prix est connu.

## Architecture

Le fonctionnement du code (quel fichier fait quoi, le cycle d'un heartbeat, le format des ordres) est décrit dans [architecture.md](architecture.md).

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
3. **Arrêt brutal.** Quand l'agent signale que le CPU de l'EC2 monte, core répond `stop` et l'agent tue le conteneur immédiatement. Il n'attend pas la fin de l'unité en cours.
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

## Comment l'ingénieur fournit son traitement

gleaner ne peut pas deviner ce que fait une unité (redimensionner une image, encoder une vidéo…). Le traitement doit donc venir de l'ingénieur. gleaner proposera deux façons de le fournir, à terme depuis une interface web.

### Maintenant : son image Docker et une commande

L'ingénieur donne :
- **une image Docker** qui contient son code et ses dépendances, par exemple `123456.dkr.ecr.eu-west-3.amazonaws.com/resize:latest` ;
- **une commande** à lancer dans cette image, par exemple `python resize.py`.

Pour chaque message SQS, gleaner lance cette commande avec le contenu du message dans la variable d'environnement `GLEANER_UNIT` :
- la commande se termine sans erreur (code de sortie 0) : gleaner supprime le message ;
- la commande échoue, ou elle est tuée par un arrêt brutal : le message n'est pas supprimé, et SQS le remet en file après le *visibility timeout*.

Le code de l'ingénieur ne contient **rien de spécifique à gleaner** : il lit une variable d'environnement et se termine. N'importe quel langage fonctionne, et ses dépendances sont déjà dans son image.

### Plus tard : un catalogue de tâches toutes faites

gleaner proposera aussi des **tâches préfaites** (par exemple « redimensionner des images », « encoder des vidéos ») : l'ingénieur choisit une tâche dans une liste et remplit ses paramètres, sans écrire de code. Ces tâches seront des images Docker fournies par gleaner, qui suivent la même règle (`GLEANER_UNIT` en entrée, code de sortie 0 en cas de succès) : elles passeront donc par le même mécanisme que les images des ingénieurs.

## Plan

On avance étape par étape. Avant les premiers clients, on ne fait que ce qui est **critique** : ce sans quoi gleaner gêne l'application du client, ne fonctionne pas chez lui, ou expose ses données. Le premier client sera sur **EC2** ; ECS viendra une fois EC2 entièrement terminé.

| Étape | Contenu | État |
|---|---|---|
| 1 | Agent : mesure du CPU et de la mémoire libres, envoi à core. Core : réception de ces informations. | ✅ |
| 2 | Enregistrement du CPU gaspillé : calcul par core à chaque heartbeat, route `GET /waste/{instance_id}` avec le coût. | ✅ |
| 3 | File SQS et dead-letter queue en Terraform. | ✅ déployée (us-east-1) |
| 4 | Worker dans l'agent : lit SQS tâche par tâche et lance l'image de l'ingénieur avec les limites CPU et mémoire. | ✅ testé avec la vraie file |
| 5 | **Ne jamais gêner l'application du client** : régler la boucle stop/start (core doit distinguer le CPU du client de celui des tâches gleaner), arrêter la tâche quand core est injoignable, nettoyer une tâche orpheline au redémarrage de l'agent, vérifier qu'un message tué revient bien dans la file. | ✅ testé avec la vraie file |
| 6 | **Héberger core** pour que les agents puissent le joindre. | ⬜ |
| 7 | **Installation sur EC2** : rôle de l'EC2 minimal (droits SQS + droit d'emprunter le rôle de la tâche), **un rôle IAM par tâche** emprunté par le worker à chaque unité (`sts:AssumeRole`, identifiants de **15 min** passés au conteneur), agent lancé en `--network host` avec le *hop limit* des métadonnées laissé à 1 pour que les tâches ne voient jamais le rôle de l'EC2, `INSTANCE_ID` lu dans les métadonnées de l'instance, image de l'agent publiée. | ⬜ |
| 8 | **Sécurité** : HTTPS entre l'agent et core, une clé par client. | ⬜ |
| 9 | **Gaspillage stocké dans DynamoDB**, pour qu'il survive aux redémarrages de core. Une fois core terminé. | ⬜ |
| 10 | **ECS**, une fois EC2 entièrement terminé. | ⬜ |

**Plus tard (utile, mais pas critique pour les premiers clients)** : une file par tâche, l'interface web, le prix du vCPU·heure via l'API AWS, le catalogue de tâches préfaites, le suivi de l'avancement d'une tâche.

## Idées à ajouter plus tard

Deux idées notées le 2026-10-16, avant une pause du projet. Elles ne sont pas encore conçues : ce qui suit est seulement ce qui est déjà décidé et les questions à trancher.

### Attribution du rôle IAM des tâches

**Le mécanisme est décidé** (étape 7) : chaque tâche a son propre rôle IAM, que le worker emprunte à chaque unité (`sts:AssumeRole`, identifiants de 15 min passés au conteneur). Le rôle de l'EC2 ne reçoit jamais les droits des tâches.

**L'idée : décider comment une tâche reçoit son rôle**, c'est-à-dire qui le crée, qui choisit ses droits, et comment gleaner le connaît. Aujourd'hui, le plus simple serait que l'ingénieur crée le rôle lui-même et donne son ARN (`TASK_ROLE_ARN`) avec son image.

**À trancher :**
- Qui crée le rôle : l'ingénieur à la main, ou gleaner à sa place ?
- Comment gleaner apprend quel rôle va avec quelle tâche (variable d'environnement, interface web, core).
- Comment s'assurer que les droits restent minimaux (un seul bucket, un seul dossier…).

### Découpage des tâches par un LLM

**Aujourd'hui, c'est l'ingénieur qui découpe sa tâche en unités** et les envoie dans SQS (voir « Le producteur » et « Tâches découpées et file SQS »). gleaner ne fait pas de découpage.

**L'idée : un LLM découpe la tâche en unités à la place de l'ingénieur.** Cela changerait la règle actuelle « gleaner ne découpe pas ».

**À trancher :**
- Ce que l'ingénieur fournit au LLM (une description en texte, son code, la liste des données à traiter…).
- Ce que produit le LLM (directement les messages SQS, ou une proposition que l'ingénieur valide).
- Comment garantir que les unités produites respectent les « Règles pour les unités » (courtes, indépendantes, rejouables sans effet de bord).
- Quel modèle utiliser et ce que coûte un découpage.
