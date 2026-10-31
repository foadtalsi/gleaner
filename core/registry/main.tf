# Public registry for the agent image: the clients' EC2s download it with docker pull
# separate folder from core/infra so that destroying the tests doesn't delete the registry and the image

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
  region = "us-east-1" # ECR Public only exists in us-east-1
}

resource "aws_ecrpublic_repository" "agent" {
  repository_name = "gleaner-agent"

  catalog_data {
    description       = "Agent gleaner : mesure les ressources libres et lance les taches"
    architectures     = ["x86-64"]
    operating_systems = ["Linux"]
  }
}

output "agent_image_uri" {
  description = "Adresse de l'image, a completer avec la version (ex : :v0.1)"
  value       = aws_ecrpublic_repository.agent.repository_uri
}
