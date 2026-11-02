# State machine + IAM role. The Lambdas, Glue job, SNS topic and Athena workgroup are
# passed in as variables so this lab stays focused on the orchestration layer.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

variable "region" { default = "ap-south-1" }
variable "validate_function_arn" { type = string }
variable "dq_function_arn" { type = string }
variable "glue_job_name" { type = string }
variable "alerts_topic_arn" { type = string }
variable "athena_workgroup" { default = "etl" }
variable "lake_bucket" { default = "bfsi-lake-dev" }

data "aws_caller_identity" "me" {}

locals {
  definition = templatefile("${path.module}/../state_machine/loan_repayments_etl.asl.json", {
    validate_function_arn = var.validate_function_arn
    dq_function_arn       = var.dq_function_arn
    glue_job_name         = var.glue_job_name
    alerts_topic_arn      = var.alerts_topic_arn
    athena_workgroup      = var.athena_workgroup
  })
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/loan-repayments-etl"
  retention_in_days = 30
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "sfn" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [var.validate_function_arn, var.dq_function_arn]
  }
  statement {
    # .sync on Glue needs Get/BatchStop as well as Start
    actions   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
    resources = ["arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:job/${var.glue_job_name}"]
  }
  statement {
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:StopQueryExecution", "athena:GetQueryResults"]
    resources = ["arn:aws:athena:${var.region}:${data.aws_caller_identity.me.account_id}:workgroup/${var.athena_workgroup}"]
  }
  statement {
    actions   = ["glue:GetTable", "glue:GetDatabase", "glue:GetPartitions"]
    resources = ["*"]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.lake_bucket}", "arn:aws:s3:::${var.lake_bucket}/*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [var.alerts_topic_arn]
  }
  statement {
    actions = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
    "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "sfn" {
  name               = "loan-repayments-etl-sfn"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "sfn" {
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_sfn_state_machine" "etl" {
  name       = "loan-repayments-etl"
  role_arn   = aws_iam_role.sfn.arn
  type       = "STANDARD" # .sync integrations and runs longer than 5 minutes need Standard
  definition = local.definition

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.etl.arn
}
