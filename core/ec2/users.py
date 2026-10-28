# Personnes qui utilisent gleaner, et les roles IAM qu'elles ont le droit de donner a une tache
# en memoire et ecrit en dur pour la demo du hackathon, les cles devront sortir du code avant de vrais clients

# liste fermee des roles du client : une tache ne peut recevoir qu'un de ces roles
# ARN provisoires, les vrais roles seront crees en Terraform
task_possible_roles = { # roles iam que les taches peuvent utiliser
    "s3-lecture": {
        "arn": "arn:aws:iam::454492134174:role/gleaner-task-s3-lecture",
        "services": "S3 : lecture",
    },
    "s3-lecture-ecriture": {
        "arn": "arn:aws:iam::454492134174:role/gleaner-task-s3-lecture-ecriture",
        "services": "S3 : lecture et ecriture",
    },
}

# allowed_roles : les roles de task_possible_roles que la personne peut donner a ses taches, modifiables par un admin
users = {
    "admin": {
        "key": "admin_key",
        "is_admin": True,
        "allowed_roles": ["s3-lecture", "s3-lecture-ecriture"],
    },
    "ingenieur_1": {
        "key": "ingenieur_1_key",
        "is_admin": False,
        "allowed_roles": ["s3-lecture", "s3-lecture-ecriture"],
    },
    "ingenieur_2": {
        "key": "ingenieur_2_key",
        "is_admin": False,
        "allowed_roles": ["s3-lecture"],
    },
}
