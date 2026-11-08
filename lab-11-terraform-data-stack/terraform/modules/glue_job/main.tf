# Glue 4.0 Spark job with its own role. The role can read the input buckets, write only under one
# prefix of the output bucket and touch only the Glue databases it is given.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  catalog_arn = "arn:${data.aws_partition.current.partition}:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:catalog"
  db_arns     = [for d in var.glue_database_names : "${replace(local.catalog_arn, ":catalog", ":database")}/${d}"]
  table_arns  = [for d in var.glue_database_names : "${replace(local.catalog_arn, ":catalog", ":table")}/${d}/*"]
  log_group   = "/aws-glue/jobs/${var.name}"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "job" {
  name               = "${var.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "job" {
  statement {
    sid       = "ReadInputs"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = flatten([for b in var.read_bucket_arns : [b, "${b}/*"]])
  }

  statement {
    sid       = "WriteOutputPrefix"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:GetObject"]
    resources = ["${var.write_bucket_arn}/${var.write_prefix}/*"]
  }

  statement {
    sid       = "ListOutputBucket"
    actions   = ["s3:ListBucket"]
    resources = [var.write_bucket_arn]
  }

  statement {
    sid = "GlueCatalog"
    actions = [
      "glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartitions",
      "glue:CreateTable", "glue:UpdateTable", "glue:BatchCreatePartition", "glue:BatchUpdatePartition",
    ]
    resources = concat([local.catalog_arn], local.db_arns, local.table_arns)
  }

  statement {
    sid       = "LakeKms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "JobLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/*"]
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "${var.name}-access"
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

resource "aws_cloudwatch_log_group" "job" {
  name              = local.log_group
  retention_in_days = 30
  tags              = var.tags
}

resource "aws_glue_job" "this" {
  name              = var.name
  role_arn          = aws_iam_role.job.arn
  glue_version      = "4.0"
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  timeout           = var.timeout_minutes
  max_retries       = 0

  command {
    name            = "glueetl"
    script_location = var.script_s3_path
    python_version  = "3"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  default_arguments = merge({
    "--job-language"                     = "python"
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--continuous-log-logGroup"          = local.log_group
    "--job-bookmark-option"              = "job-bookmark-enable"
  }, var.default_arguments)

  tags = var.tags
}
