variable "region" {
  description = "Region AWS ou la file SQS est creee"
  type        = string
  default     = "us-east-1"
}

variable "visibility_timeout_seconds" {
  description = "Temps pendant lequel un message lu reste invisible. Doit etre plus long que la duree d'une unite"
  type        = number
  default     = 900 # 15 min
}

variable "max_receive_count" {
  description = "Nombre de receptions avant d'envoyer un message dans la DLQ. Chaque arret brutal compte comme une reception"
  type        = number
  default     = 10
}
