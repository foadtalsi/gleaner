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
| 6 | **Héberger core** pour que les agents puissent le joindre : EC2 `t3.micro` (`core/hosting/`), Elastic IP, domaine `api.talsi.dev`. | ✅ testé avec une EC2 d'agent (arrêté en attendant la clé par client) |
| 7 | **Installation sur EC2** : rôle de l'EC2 minimal (droits SQS + droit d'emprunter le rôle de la tâche), **un rôle IAM par tâche** emprunté par le worker à chaque unité (`sts:AssumeRole`, identifiants de **15 min** passés au conteneur), agent lancé en `--network host` avec le *hop limit* des métadonnées laissé à 1 pour que les tâches ne voient jamais le rôle de l'EC2, `INSTANCE_ID` lu dans les métadonnées de l'instance, image de l'agent publiée. | ✅ testé sur une vraie EC2 (image `public.ecr.aws/a3i1n1h9/gleaner-agent:v0.1`) |
| 8 | **Sécurité** : HTTPS entre l'agent et core (Caddy, certificat Let's Encrypt), une clé par client. | 🟡 HTTPS fait, clé par client à faire |
| 9 | **Gaspillage stocké dans DynamoDB**, pour qu'il survive aux redémarrages de core. Une fois core terminé. | ⬜ |
| 10 | **ECS**, une fois EC2 entièrement terminé. | ⬜ |

**Plus tard (utile, mais pas critique pour les premiers clients)** : une file par tâche, l'interface web, le prix du vCPU·heure via l'API AWS, le catalogue de tâches préfaites, le suivi de l'avancement d'une tâche.

## Hackathon Meta : du 27 octobre au 4 novembre 2026

Rendu le **mercredi 4 novembre**. Crédits : 150 $ sur l'API Llama de Meta. AWS n'est pas couvert par ces crédits (compter environ 10 à 20 $, EC2 allumées seulement pour les tests et la démo).

### Le but

```
Interface web (https://api.talsi.dev)
  1. l'utilisateur décrit sa tâche : "redimensionne les photos du bucket demo en 800px"
  2. core liste les images du bucket
  3. le LLM propose : le CODE de la tâche + le découpage (lots d'images) + le rôle IAM choisi dans une liste fermée
  4. l'humain vérifie le code, le découpage et le rôle, peut changer le rôle, puis valide
  5. core met les lots dans SQS → les agents des EC2 exécutent le code sur chaque lot, avec ce rôle
  6. l'interface montre l'avancement et le compute récupéré
```

Démo : **redimensionnement d'images**.

**Décidé le 2026-10-27 : c'est le LLM qui écrit le code de la tâche** (`create_task_code` dans `core/ec2/llm.py`), à partir de sa description. L'ingénieur ne fournit plus son image Docker. Conséquences :
- **Une image gleaner générique** (Python + `boto3` + `Pillow`) exécute le code généré. Le code ne peut utiliser que ces bibliothèques.
- **Le code ne traite qu'une unité** : il la lit dans la variable `GLEANER_TASK`, et ne liste pas lui-même tout le bucket.
- **L'humain valide aussi le code** avant le lancement : il tourne sur la machine du client. Le rôle IAM limite les dégâts si le code est mauvais ou si la description était piégée.
- **Le code arrive au worker dans chaque message SQS** : `{"input_resources": [...], "output_resources": [...], "keys": [...], "code": ..., "iam_role": ...}`, environ 6 Ko par message (limite SQS : 256 Ko). Le worker le lance avec `python -c` dans l'image générique (`TASK_IMAGE`).
- **Décidé le 2026-10-31 : l'ingénieur donne deux listes de ressources.** `input_resources` (ex : `s3://bucket/photos/`) : lues seulement, et découpées en lots par core. `output_resources` (ex : `s3://bucket/resized/`) : où le code écrit ses résultats, jamais découpées. Avec une seule liste, soit l'écriture était refusée, soit core découpait aussi les résultats et le code pouvait écraser les originaux.
- **Le worker limite le rôle à ces ressources** avec une Policy de session (paramètre `Policy` de `sts.assume_role`) : `s3:GetObject` sur les entrées, `s3:PutObject` sur les sorties, rien d'autre. Même un code malveillant ne peut ni écraser les originaux, ni lire ailleurs. Vérifié avec le simulateur IAM (`aws iam simulate-custom-policy`).

### Le moindre privilège sur les tâches

**Décidé le 2026-10-27**, pour se démarquer au hackathon :

```
admin        → limite les rôles que chaque personne peut donner
LLM          → choisit le plus petit rôle parmi ceux-là
revue        → une autre personne valide le code et le rôle, obligatoirement
exécution    → rôle limité aux ressources déclarées, identifiants de 15 min
```

- **Des personnes et des rôles dans le produit** : chaque personne a une clé et une liste de **rôles IAM autorisés**, pris dans la liste fermée du client. Gardé en mémoire (`users`), écrit en dur pour la démo (un admin, deux ingénieurs). Cette clé remplace le `client_key` provisoire.
- **L'admin modifie les rôles autorisés** de chaque personne (route réservée aux admins, puis page de l'interface). La limite porte sur des **rôles IAM**, pas sur des services AWS : c'est le rôle qui donne réellement les droits. L'interface affiche les services de chaque rôle.
- **Le LLM ne voit que les rôles autorisés de l'auteur de la tâche**, et core refuse un rôle hors de cette liste.
- **Revue obligatoire par une autre personne que l'auteur** (règle des « 4 yeux ») : `POST /task` garde la tâche avec l'état `"a_valider"` sans rien envoyer dans SQS. Seule la route `POST /tasks/{task_id}/validate` appelle `send_task_to_sqs`, puis passe la tâche en `"validee"` pour qu'elle ne parte pas deux fois. Le relecteur voit le code et le rôle, et peut changer le rôle pour un autre rôle autorisé (changement de rôle pas encore fait).

### Simplifications pour tenir les délais

- **Une seule tâche active à la fois** et une seule file SQS.
- **Le LLM ne fabrique pas les unités** : il renvoie un petit JSON (rôle choisi + taille des lots), et c'est **core qui construit les unités** à partir des images listées. Cela répond à « d'où le LLM connaît les données ».
- **Core vérifie la réponse du LLM** : un rôle hors de la liste est refusé, et l'humain choisit lui-même.
- **L'humain valide le code, le rôle et le découpage** dans l'interface web.
- **Tout reste en mémoire** : ni DynamoDB ni ECS pendant le hackathon.
- **L'interface est une simple page HTML** servie par FastAPI.
- **C'est l'interface web qui crée la tâche** : elle envoie à core (`POST /task`) l'identifiant de la tâche, sa description et la clé du client. Core ne fabrique pas l'identifiant, il garde celui qu'il reçoit. Pour le moment, core vérifie cette clé dans la liste des clés d'agent (`AGENTS_KEY_DIR`) ; une vraie liste de clés client viendra plus tard.
- **Appels au LLM de Meta (`muse-spark-1.3`, API `api.meta.ai`) avec `requests`**. La clé est dans `core/ec2/.env`, ignoré par git, et chargée en variable d'environnement. Chaque appel est limité à 16 000 tokens de sortie (`MAX_OUTPUT_TOKENS`) : la réflexion du modèle compte dedans, et à 4 000 il ne restait rien pour le code.

### Planning

| Jour | Contenu | État |
|---|---|---|
| Mar. 27 | Premier appel au LLM depuis core (`core/ec2/llm.py`, modèle `muse-spark-1.3` sur `api.meta.ai`). Clés client (fait par Foad). | ✅ appel testé |
| Mer. 28 | Notion de **tâche** dans core (`POST /task`, champ `resources` donné par l'ingénieur). **Génération du code par le LLM** (`create_task_code`). Découpage en lots de 20 et envoi dans SQS (`send_task_to_sqs`, le code voyage dans chaque message). | ✅ testé le 27 (45 photos → 3 messages) |
| Jeu. 29 | **Personnes et rôles autorisés** (`users` en mémoire, route admin `PUT /users/{name}/allowed_roles`). **Choix du rôle par le LLM** parmi les rôles autorisés de l'auteur, vérifié par core. `POST /task` garde la tâche avec le code, le rôle et l'état `"a_valider"`, sans rien envoyer dans SQS. Le rôle (son ARN) voyage dans chaque message SQS. | ✅ testé le 28 |
| Ven. 30 | **Revue obligatoire** par une autre personne (`POST /tasks/{task_id}/validate`), seule à envoyer dans SQS. Ressources à lire et à écrire séparées. Le worker exécute le code généré dans l'image générique, avec le rôle limité aux ressources. | ✅ revue testée de bout en bout le 29 (25 fichiers → 2 messages), Policy vérifiée avec le simulateur IAM, exécution testée en local. `assume_role` à tester avec les vrais rôles du 1er |
| Sam. 31 | **Interface web** : formulaire, écran de revue (code, rôle, découpage), page admin, suivi. | ⬜ |
| Dim. 1 | **Image gleaner générique** (Python + boto3 + Pillow, publiée sur ECR Public) et 2 ou 3 rôles de démo en Terraform. | ⬜ |
| Lun. 2 | **Déploiement** : core sur `api.talsi.dev`, 2 EC2 avec l'agent, un bucket de photos. Test de bout en bout. À corriger avant : `core/hosting/main.tf` ne copie pas `llm.py`, `users.py` ni `agent_key.py`, le Dockerfile de core n'installe ni `boto3` ni `requests`, et ni `meta_api_key` ni `QUEUE_URL` ne sont passés à core. | ⬜ |
| Mar. 3 | **Démo solide** et enregistrée : vidéo, README, pitch. | ⬜ |
| Mer. 4 | **Soumission**, et marge si quelque chose a glissé. | ⬜ |

## Idées à ajouter plus tard

Deux idées notées le 2026-10-16, avant une pause du projet. Elles ne sont pas encore conçues : ce qui suit est seulement ce qui est déjà décidé et les questions à trancher.

### Attribution du rôle IAM des tâches

**Le mécanisme est décidé** (étape 7) : le worker emprunte le rôle de la tâche à chaque unité (`sts:AssumeRole`, identifiants de 15 min passés au conteneur). Le rôle de l'EC2 ne reçoit jamais les droits des tâches.

**Décidé le 2026-10-19 : un LLM choisit le rôle dans une liste fermée, un humain valide.**

1. **Le client définit à l'avance une liste de rôles autorisés**, chacun avec des droits précis (ex. `lecture-s3-photos`, `ecriture-s3-resultats`). Aucun autre rôle ne peut être utilisé : le pire cas possible est le rôle le plus large de la liste.
2. **Le LLM lit la description de la tâche** (seulement la description) et choisit le rôle de la liste qui correspond.
3. **Un humain voit le rôle choisi par le LLM et peut le changer** avant de lancer la tâche. Le LLM ne décide jamais seul : une erreur de choix ou une injection de prompt dans la description est rattrapée à cette validation.

**À trancher :**
- Où le client définit la liste (fichier, Terraform, interface web) et où elle est stockée.
- Où l'humain valide (interface web, ligne de commande).
- Quel modèle utiliser.

### Découpage des tâches par un LLM

**Aujourd'hui, c'est l'ingénieur qui découpe sa tâche en unités** et les envoie dans SQS (voir « Le producteur » et « Tâches découpées et file SQS »). gleaner ne fait pas de découpage.

**Décidé le 2026-10-19 : c'est le même LLM qui choisit le rôle et qui découpe la tâche**, à partir de sa description. Cela change la règle actuelle « gleaner ne découpe pas ».

**À trancher :**
- **D'où le LLM connaît les données à découper.** La description dit *quoi* faire (« redimensionner les images du bucket mes-photos »), mais pas la liste des 10 000 fichiers : il faudra lister les données (ex. les objets du bucket) pour produire une unité par élément.
- Si l'humain valide aussi le découpage, en même temps que le rôle.
- Comment garantir que les unités produites respectent les « Règles pour les unités » (courtes, indépendantes, rejouables sans effet de bord).
- Ce que coûte un découpage.
