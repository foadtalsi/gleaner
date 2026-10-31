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

# Elastic IP created by hand, already used by the DNS: Terraform reuses it without managing it
data "aws_eip" "core" {
  public_ip = "54.243.111.171"
}

data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
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
  vpc_security_group_ids = [aws_security_group.core.id]

  metadata_options {
    http_tokens = "required"
  }

  # at boot: installs docker, copies core's code, builds its image, starts core then Caddy
  # the script is written at column 0: bash requires #!/bin/bash and the GLEANER_EOF at the start of the line
  user_data_replace_on_change = true
  user_data                   = <<EOT
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
docker build -t gleaner-core .
docker run -d --name gleaner-core --restart unless-stopped -p 127.0.0.1:8000:8000 gleaner-core

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

  tags = {
    Name = "gleaner-core"
  }
}

resource "aws_eip_association" "core" {
  instance_id   = aws_instance.core.id
  allocation_id = data.aws_eip.core.id
}

output "core_url" {
  description = "Adresse de core a donner aux agents (CORE_URL)"
  value       = "https://${var.domain}"
}
