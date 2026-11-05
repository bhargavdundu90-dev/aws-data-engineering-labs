terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "name" {
  type    = string
  default = "lab08"
}

variable "stream_mode" {
  description = "ON_DEMAND for spiky or unknown traffic, PROVISIONED when the load is steady and sized"
  type        = string
  default     = "ON_DEMAND"
}

variable "shard_count" {
  description = "Only used in PROVISIONED mode (see producer.shards_needed)"
  type        = number
  default     = 2
}

data "aws_caller_identity" "me" {}

resource "aws_kinesis_stream" "txns" {
  name             = "${var.name}-transactions"
  retention_period = 48 # hours; room to replay a bad deploy from TRIM_HORIZON
  shard_count      = var.stream_mode == "PROVISIONED" ? var.shard_count : null
  encryption_type  = "KMS"
  kms_key_id       = "alias/aws/kinesis"

  stream_mode_details {
    stream_mode = var.stream_mode
  }
}

resource "aws_s3_bucket" "lake" {
  bucket = "${var.name}-txn-lake-${data.aws_caller_identity.me.account_id}"
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_sqs_queue" "consumer_dlq" {
  name                      = "${var.name}-kinesis-consumer-dlq"
  message_retention_seconds = 1209600
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "consumer" {
  name               = "${var.name}-kinesis-consumer"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "kinesis_exec" {
  role       = aws_iam_role.consumer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaKinesisExecutionRole"
}

data "aws_iam_policy_document" "consumer" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/curated/*", "${aws_s3_bucket.lake.arn}/quarantine/*"]
  }
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.consumer_dlq.arn]
  }
}

resource "aws_iam_role_policy" "consumer" {
  role   = aws_iam_role.consumer.id
  policy = data.aws_iam_policy_document.consumer.json
}

data "archive_file" "consumer" {
  type        = "zip"
  source_file = "${path.module}/../src/consumer.py"
  output_path = "${path.module}/build/consumer.zip"
}

resource "aws_lambda_function" "consumer" {
  function_name    = "${var.name}-kinesis-to-s3"
  role             = aws_iam_role.consumer.arn
  runtime          = "python3.12"
  handler          = "consumer.lambda_handler"
  filename         = data.archive_file.consumer.output_path
  source_code_hash = data.archive_file.consumer.output_base64sha256
  timeout          = 60
  memory_size      = 256
  environment {
    variables = { BUCKET = aws_s3_bucket.lake.id }
  }
}

resource "aws_lambda_event_source_mapping" "txns" {
  event_source_arn  = aws_kinesis_stream.txns.arn
  function_name     = aws_lambda_function.consumer.arn
  starting_position = "LATEST"

  batch_size                         = 500
  maximum_batching_window_in_seconds = 10 # fewer, larger S3 objects
  parallelization_factor             = 2  # two concurrent batches per shard, order kept per partition key

  function_response_types        = ["ReportBatchItemFailures"]
  bisect_batch_on_function_error = true
  maximum_retry_attempts         = 5
  maximum_record_age_in_seconds  = 3600

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.consumer_dlq.arn
    }
  }
}

output "stream_name" {
  value = aws_kinesis_stream.txns.name
}

output "bucket" {
  value = aws_s3_bucket.lake.id
}
