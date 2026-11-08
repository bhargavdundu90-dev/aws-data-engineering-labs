# One data domain's stack: KMS key, raw / curated / athena-results buckets, a Glue database per zone,
# the raw -> curated Glue job and an Athena workgroup that enforces its output location.

locals {
  prefix = "${var.org}-${var.domain}-${var.environment}"
  tags = {
    owner               = var.owner
    cost_center         = var.cost_center
    data_classification = var.data_classification
    environment         = var.environment
    domain              = var.domain
    managed_by          = "terraform"
  }
  is_prod = var.environment == "prod"
}

resource "aws_kms_key" "lake" {
  description             = "${local.prefix} lake key"
  enable_key_rotation     = true
  deletion_window_in_days = local.is_prod ? 30 : 7
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${local.prefix}-lake"
  target_key_id = aws_kms_key.lake.key_id
}

module "raw" {
  source            = "../modules/lake_bucket"
  name              = "${local.prefix}-raw"
  zone              = "raw"
  kms_key_arn       = aws_kms_key.lake.arn
  ia_after_days     = 30
  expire_after_days = var.raw_retention_days
  force_destroy     = !local.is_prod
}

module "curated" {
  source          = "../modules/lake_bucket"
  name            = "${local.prefix}-curated"
  zone            = "curated"
  kms_key_arn     = aws_kms_key.lake.arn
  noncurrent_days = local.is_prod ? 90 : 7
  force_destroy   = !local.is_prod
}

module "athena_results" {
  source            = "../modules/lake_bucket"
  name              = "${local.prefix}-athena-results"
  zone              = "athena-results"
  kms_key_arn       = aws_kms_key.lake.arn
  noncurrent_days   = 1
  expire_after_days = 7
  force_destroy     = true
}

module "raw_db" {
  source        = "../modules/glue_catalog"
  database_name = replace("${var.domain}_raw_${var.environment}", "-", "_")
  description   = "Raw ${var.domain} files as landed"
  location      = "s3://${module.raw.bucket}/"
}

module "curated_db" {
  source        = "../modules/glue_catalog"
  database_name = replace("${var.domain}_curated_${var.environment}", "-", "_")
  description   = "Cleaned, typed and partitioned ${var.domain} tables"
  location      = "s3://${module.curated.bucket}/"
}

module "curate_job" {
  source              = "../modules/glue_job"
  name                = "${local.prefix}-curate"
  script_s3_path      = "s3://${module.raw.bucket}/_code/curate.py"
  read_bucket_arns    = [module.raw.arn]
  write_bucket_arn    = module.curated.arn
  write_prefix        = "tables"
  glue_database_names = [module.raw_db.name, module.curated_db.name]
  kms_key_arn         = aws_kms_key.lake.arn
  number_of_workers   = var.glue_workers
  default_arguments = {
    "--raw_db"     = module.raw_db.name
    "--curated_db" = module.curated_db.name
  }
}

resource "aws_athena_workgroup" "analysts" {
  name          = "${local.prefix}-analysts"
  force_destroy = !local.is_prod

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    # stop runaway scans: 10 GB per query in prod, 1 GB in dev
    bytes_scanned_cutoff_per_query = local.is_prod ? 10737418240 : 1073741824

    result_configuration {
      output_location = "s3://${module.athena_results.bucket}/results/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.lake.arn
      }
    }
  }
}
