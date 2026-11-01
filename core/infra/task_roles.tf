# the client's closed list of task roles, the ones listed in core/ec2/users.py (task_possible_roles)
# both only reach the test bucket, and only the agent ec2's role can assume them
# at run time the worker cuts them down again to the declared resources (session policy)

resource "aws_iam_role" "task_s3_lecture" {
  name = "gleaner-task-s3-lecture"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.agent_ec2.arn }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task_s3_lecture" {
  name = "gleaner-task-s3-lecture"
  role = aws_iam_role.task_s3_lecture.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = "${aws_s3_bucket.task_test.arn}/*"
    }]
  })
}


resource "aws_iam_role" "task_s3_lecture_ecriture" {
  name = "gleaner-task-s3-lecture-ecriture"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.agent_ec2.arn }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task_s3_lecture_ecriture" {
  name = "gleaner-task-s3-lecture-ecriture"
  role = aws_iam_role.task_s3_lecture_ecriture.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject"]
      Resource = "${aws_s3_bucket.task_test.arn}/*"
    }]
  })
}
