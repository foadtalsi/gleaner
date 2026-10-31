resource "aws_sqs_queue" "task_queue" {
  name = "task_queue"

  # if the unit isn't deleted before this delay, it comes back to the queue
  visibility_timeout_seconds = var.visibility_timeout_seconds
  # long polling: the worker waits up to 20 s for a message instead of looping empty
  receive_wait_time_seconds = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq_task_queue.arn
    maxReceiveCount     = var.max_receive_count
  })
}


resource "aws_sqs_queue" "dlq_task_queue" { # queue for tasks that failed many times
  name = "dlq_task_queue"

  message_retention_seconds = 1209600 # 14 days (the max), to have time to analyze the failures
}

# only task_queue is allowed to send its messages to the DLQ
resource "aws_sqs_queue_redrive_allow_policy" "dlq_task_queue" {
  queue_url = aws_sqs_queue.dlq_task_queue.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.task_queue.arn]
  })
}
