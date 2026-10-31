# People who use gleaner, and the IAM roles they are allowed to give to a task
# in memory and hardcoded for the hackathon demo, the keys must leave the code before real clients

# the client's closed list of roles: a task can only receive one of these roles
# temporary ARNs, the real roles will be created in Terraform
task_possible_roles = { # iam roles that tasks can use
    "s3-lecture": {
        "arn": "arn:aws:iam::454492134174:role/gleaner-task-s3-lecture",
        "services": "S3 : lecture",
    },
    "s3-lecture-ecriture": {
        "arn": "arn:aws:iam::454492134174:role/gleaner-task-s3-lecture-ecriture",
        "services": "S3 : lecture et ecriture",
    },
}

# allowed_roles: the roles from task_possible_roles that the person can give to their tasks, editable by an admin
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
