# People who use gleaner, and the IAM roles they are allowed to give to a task
# in memory and hardcoded for the hackathon demo, the keys must leave the code before real clients

# the client's closed list of roles: a task can only receive one of these roles
# the roles are created in core/infra/task_roles.tf, their names must match these ARNs
task_possible_roles = { # iam roles that tasks can use
    "s3-read": {
        "arn": "arn:aws:iam::454492134174:role/gleaner-task-s3-lecture",
        "services": "S3: read",
    },
    "s3-read-write": {
        "arn": "arn:aws:iam::454492134174:role/gleaner-task-s3-lecture-ecriture",
        "services": "S3: read and write",
    },
}

# allowed_roles: the roles from task_possible_roles that the person can give to their tasks, editable by an admin
users = {
    "admin": {
        "key": "admin_key",
        "is_admin": True,
        "allowed_roles": ["s3-read", "s3-read-write"],
    },
    "engineer_1": {
        "key": "engineer_1_key",
        "is_admin": False,
        "allowed_roles": ["s3-read", "s3-read-write"],
    },
    "engineer_2": {
        "key": "engineer_2_key",
        "is_admin": False,
        "allowed_roles": ["s3-read"],
    },
}
