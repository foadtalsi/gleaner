# Registre public de l'image de l'agent : les EC2 des clients la telechargent avec docker pull
# dossier separe de core/infra pour que le destroy des tests ne supprime pas le registre et l'image

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
  region = "us-east-1" # ECR Public n'existe qu'en us-east-1
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
