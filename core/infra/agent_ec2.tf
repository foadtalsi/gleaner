# EC2 de test qui joue la machine d'un client : l'agent gleaner tourne dessus
# core tourne sur le mac, joignable par un tunnel cloudflared (variable core_url)

variable "core_url" {
  description = "Adresse publique de core (tunnel cloudflared vers le mac), ex : https://xxx.trycloudflare.com"
  type        = string
}

variable "task_image" {
  description = "Image docker de l'ingenieur lancee pour chaque unite"
  type        = string
  default     = "alpine"
}

data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}


# role de l'ec2 : seulement lire et supprimer les messages de la file
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


# aucun port ouvert en entree : l'agent ne fait que des connexions sortantes (core, SQS, docker hub)
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


resource "aws_instance" "agent" {
  ami                    = data.aws_ssm_parameter.amazon_linux.value
  instance_type          = "t3.small"
  iam_instance_profile   = aws_iam_instance_profile.agent_ec2.name
  vpc_security_group_ids = [aws_security_group.agent_ec2.id]

  # hop limit a 1 : seul l'agent (en --network host) atteint les metadonnees, pas les conteneurs des taches
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # au demarrage : installe docker, copie le code de l'agent, construit l'image et lance l'agent
  # compresse avec gzip : sinon le code de l'agent depasse la limite de 16 Ko des user_data
  user_data_replace_on_change = true
  # le script est ecrit en colonne 0 : bash exige le #!/bin/bash et les GLEANER_EOF en debut de ligne
  user_data_base64 = base64gzip(<<EOT
#!/bin/bash
dnf install -y docker
systemctl enable --now docker

mkdir -p /opt/gleaner-agent
cd /opt/gleaner-agent
cat > agent.py <<'GLEANER_EOF'
${file("${path.module}/../../agent/ec2/agent.py")}
GLEANER_EOF
cat > worker.py <<'GLEANER_EOF'
${file("${path.module}/../../agent/ec2/worker.py")}
GLEANER_EOF
cat > requirements.txt <<'GLEANER_EOF'
${file("${path.module}/../../agent/ec2/requirements.txt")}
GLEANER_EOF
cat > Dockerfile <<'GLEANER_EOF'
${file("${path.module}/../../agent/ec2/Dockerfile")}
GLEANER_EOF
docker build -t gleaner-agent .

TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)

docker run -d --name gleaner-agent --restart unless-stopped --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CORE_URL=${var.core_url} \
  -e INSTANCE_ID=$INSTANCE_ID \
  -e AWS_DEFAULT_REGION=${var.region} \
  -e QUEUE_URL=${aws_sqs_queue.task_queue.url} \
  -e TASK_IMAGE=${var.task_image} \
  gleaner-agent
EOT
  )

  tags = {
    Name = "gleaner-agent-test"
  }
}

output "agent_instance_id" {
  description = "Id de l'EC2 de test, c'est aussi l'instance_id que l'agent envoie a core"
  value       = aws_instance.agent.id
}
