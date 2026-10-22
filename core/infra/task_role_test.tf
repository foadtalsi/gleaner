# Test du role par tache : un bucket de test, et le role gleaner-task-exemple qui n'a acces qu'a ce bucket
# core envoie l'arn de ce role dans l'Order (voir task_iam_roles dans core/ec2/scheduler.py)

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "task_test" {
  bucket        = "gleaner-task-test-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # destroy supprime aussi les fichiers du test
}


# le role de la tache : seul le role de l'ec2 de l'agent peut l'emprunter
resource "aws_iam_role" "task_exemple" {
  name = "gleaner-task-exemple"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.agent_ec2.arn }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task_exemple_s3" {
  name = "gleaner-task-exemple-s3"
  role = aws_iam_role.task_exemple.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.task_test.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.task_test.arn}/*"
      }
    ]
  })
}


# le role de l'ec2 peut emprunter ce role, et rien de plus
resource "aws_iam_role_policy" "agent_ec2_assume_task" {
  name = "gleaner-agent-assume-task"
  role = aws_iam_role.agent_ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = aws_iam_role.task_exemple.arn
    }]
  })
}

output "task_test_bucket" {
  description = "Bucket de test : la tache copie entree.txt vers sortie.txt"
  value       = aws_s3_bucket.task_test.bucket
}
