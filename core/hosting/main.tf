# EC2 hosting core, reachable over HTTPS at api.talsi.dev
# Caddy receives the HTTPS (automatic Let's Encrypt certificate) and forwards to core on 127.0.0.1:8000
# separate folder from core/infra so that destroying the tests doesn't delete core

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  required_version = ">= 1.3.0"
}

provider "aws" {
  region = "us-east-1"
}

variable "domain" {
  description = "Nom de domaine de core, son enregistrement A chez Cloudflare pointe vers l'Elastic IP (nuage gris)"
  type        = string
  default     = "api.talsi.dev"
}

# given with TF_VAR_meta_api_key loaded from core/ec2/.env, never written in a file of the repo
# it ends up in clear in the EC2's user_data and in terraform.tfstate: acceptable for the demo, rotate the key afterwards
variable "meta_api_key" {
  description = "Cle de l'API Meta, utilisee par llm.py"
  type        = string
  sensitive   = true
}

# IP of core: after each apply, put the core_ip output in the A record of api.talsi.dev at Cloudflare
resource "aws_eip" "core" {
  domain = "vpc"

  tags = {
    Name = "gleaner-core"
  }
}

data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

data "aws_caller_identity" "current" {}


# core's role: list the files of the photo bucket to cut the task, and send the batches to the queue
# the queue and the bucket live in core/infra, so they are written by name: this folder doesn't depend on its state
resource "aws_iam_role" "core" {
  name = "gleaner-core"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "core" {
  name = "gleaner-core-sqs-s3"
  role = aws_iam_role.core.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = "arn:aws:sqs:us-east-1:${data.aws_caller_identity.current.account_id}:task_queue"
      },
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = "arn:aws:s3:::gleaner-task-test-${data.aws_caller_identity.current.account_id}"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "core" {
  name = "gleaner-core"
  role = aws_iam_role.core.name
}


# only 80 (Let's Encrypt) and 443 (HTTPS from the agents): core's port 8000 stays closed
resource "aws_security_group" "core" {
  name        = "gleaner-core"
  description = "Core gleaner : HTTP et HTTPS pour Caddy"

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}


resource "aws_instance" "core" {
  ami                    = data.aws_ssm_parameter.amazon_linux.value
  instance_type          = "t3.micro"
  iam_instance_profile   = aws_iam_instance_profile.core.name
  vpc_security_group_ids = [aws_security_group.core.id]

  # hop limit of 2: core runs in a container (bridge network), it needs one more hop to get the role's credentials
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  # at boot: installs docker, copies core's code, builds its image, starts core then Caddy
  # the script is written at column 0: bash requires #!/bin/bash and the GLEANER_EOF at the start of the line
  # gzipped because AWS refuses user_data over 16 KB, and the code of core weighs 45 KB
  user_data_replace_on_change = true
  user_data_base64            = base64gzip(<<EOT
#!/bin/bash
dnf install -y docker
systemctl enable --now docker

mkdir -p /opt/gleaner-core
cd /opt/gleaner-core
cat > Dockerfile <<'GLEANER_EOF'
${file("${path.module}/../ec2/Dockerfile")}
GLEANER_EOF
cat > task_manager_api.py <<'GLEANER_EOF'
${file("${path.module}/../ec2/task_manager_api.py")}
GLEANER_EOF
cat > scheduler.py <<'GLEANER_EOF'
${file("${path.module}/../ec2/scheduler.py")}
GLEANER_EOF
cat > instances.py <<'GLEANER_EOF'
${file("${path.module}/../ec2/instances.py")}
GLEANER_EOF
cat > waste.py <<'GLEANER_EOF'
${file("${path.module}/../ec2/waste.py")}
GLEANER_EOF
cat > llm.py <<'GLEANER_EOF'
${file("${path.module}/../ec2/llm.py")}
GLEANER_EOF
cat > users.py <<'GLEANER_EOF'
${file("${path.module}/../ec2/users.py")}
GLEANER_EOF
cat > agent_key.py <<'GLEANER_EOF'
${file("${path.module}/../ec2/agent_key.py")}
GLEANER_EOF
cat > index.html <<'GLEANER_EOF'
${file("${path.module}/../ec2/index.html")}
GLEANER_EOF
docker build -t gleaner-core .
docker run -d --name gleaner-core --restart unless-stopped -p 127.0.0.1:8000:8000 \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e QUEUE_URL=https://sqs.us-east-1.amazonaws.com/${data.aws_caller_identity.current.account_id}/task_queue \
  -e meta_api_key='${var.meta_api_key}' \
  gleaner-core

mkdir -p /opt/caddy
cat > /opt/caddy/Caddyfile <<'GLEANER_EOF'
${var.domain} {
  reverse_proxy 127.0.0.1:8000
}
GLEANER_EOF
docker run -d --name caddy --restart unless-stopped --network host \
  -v /opt/caddy/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy_data:/data \
  caddy:2
EOT
  )

  tags = {
    Name = "gleaner-core"
  }
}

resource "aws_eip_association" "core" {
  instance_id   = aws_instance.core.id
  allocation_id = aws_eip.core.id
}

output "core_ip" {
  description = "IP a mettre dans l'enregistrement A de api.talsi.dev chez Cloudflare (nuage gris)"
  value       = aws_eip.core.public_ip
}

output "core_url" {
  description = "Adresse de core a donner aux agents (CORE_URL)"
  value       = "https://${var.domain}"
}
