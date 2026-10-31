# Test of the per-task role: a test bucket, and the gleaner-task-exemple role which only has access to this bucket
# core sends this role's arn in the Order (see task_iam_roles in core/ec2/scheduler.py)

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "task_test" {
  bucket        = "gleaner-task-test-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # destroy also deletes the test files
}


# the task's role: only the agent ec2's role can assume it
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


# the ec2's role can assume this role, and nothing more
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
