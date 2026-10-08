resource "aws_sqs_queue" "task_queue" {
  name = "task_queue"

  # si l'unite n'est pas supprimee avant ce delai, elle revient dans la file
  visibility_timeout_seconds = var.visibility_timeout_seconds
  # long polling : le worker attend jusqu'a 20 s qu'un message arrive au lieu de boucler a vide
  receive_wait_time_seconds = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq_task_queue.arn
    maxReceiveCount     = var.max_receive_count
  })
}


resource "aws_sqs_queue" "dlq_task_queue" { # queue pour les taches qui ont echouer plein de fois
  name = "dlq_task_queue"

  message_retention_seconds = 1209600 # 14 jours (le max), pour avoir le temps d'analyser les echecs
}

# seule task_queue a le droit d'envoyer ses messages dans la DLQ
resource "aws_sqs_queue_redrive_allow_policy" "dlq_task_queue" {
  queue_url = aws_sqs_queue.dlq_task_queue.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.task_queue.arn]
  })
}
