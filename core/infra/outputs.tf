output "task_queue_url" {
  description = "URL de la file ou l'ingenieur envoie ses unites"
  value       = aws_sqs_queue.task_queue.url
}

output "dlq_task_queue_url" {
  description = "URL de la DLQ, pour voir les unites qui ont echoue trop souvent"
  value       = aws_sqs_queue.dlq_task_queue.url
}
