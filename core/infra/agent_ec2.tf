# test EC2 that plays a client's machine: the gleaner agent runs on it
# core runs on the mac, reachable through a cloudflared tunnel (variable core_url)

variable "core_url" {
  description = "Adresse publique de core (tunnel cloudflared vers le mac), ex : https://xxx.trycloudflare.com"
  type        = string
}

variable "agent_image" {
  description = "Image publique de l'agent, publiee par core/registry"
  type        = string
  default     = "public.ecr.aws/a3i1n1h9/gleaner-agent:v0.3"
}

data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}


# the ec2's role: only read and delete messages from the queue
resource "aws_iam_role" "agent_ec2" {
  name = "gleaner-agent-ec2"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "agent_ec2_sqs" {
  name = "gleaner-agent-sqs"
  role = aws_iam_role.agent_ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage"]
      Resource = aws_sqs_queue.task_queue.arn
    }]
  })
}

resource "aws_iam_instance_profile" "agent_ec2" {
  name = "gleaner-agent-ec2"
  role = aws_iam_role.agent_ec2.name
}


# no inbound port open: the agent only makes outbound connections (core, SQS, docker hub)
resource "aws_security_group" "agent_ec2" {
  name        = "gleaner-agent-ec2"
  description = "Agent gleaner : aucune entree, sorties autorisees"

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}


# 2 identical machines, to show several agents sharing the batches of a task through the queue
resource "aws_instance" "agent" {
  count                  = 2
  ami                    = data.aws_ssm_parameter.amazon_linux.value
  instance_type          = "t3.small"
  iam_instance_profile   = aws_iam_instance_profile.agent_ec2.name
  vpc_security_group_ids = [aws_security_group.agent_ec2.id]

  # hop limit of 1: only the agent (in --network host) reaches the metadata, not the task containers
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # at boot: installs docker, downloads the agent's public image and runs it
  user_data_replace_on_change = true
  user_data                   = <<-EOT
    #!/bin/bash
    dnf install -y docker
    systemctl enable --now docker

    TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
    INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)

    docker run -d --name gleaner-agent --restart unless-stopped --network host \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -e CORE_URL=${var.core_url} \
      -e INSTANCE_ID=$INSTANCE_ID \
      -e AWS_DEFAULT_REGION=${var.region} \
      -e QUEUE_URL=${aws_sqs_queue.task_queue.url} \
      -e TASK_IMAGE=${var.agent_image} \
      ${var.agent_image}
  EOT

  tags = {
    Name = "gleaner-agent-test-${count.index}"
  }
}

output "agent_instance_ids" {
  description = "Ids des EC2 de test, ce sont aussi les instance_id que les agents envoient a core"
  value       = aws_instance.agent[*].id
}
