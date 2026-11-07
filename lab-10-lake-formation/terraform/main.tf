# Lake Formation governance for the lending_curated database.
# Same model as policy/lake_policy.yml: LF-tags on database/columns, tag-based grants,
# and a data cell filter for the South branch operations team.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
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

variable "lake_bucket_arn" {
  description = "S3 location of the curated zone, registered with Lake Formation"
  type        = string
}

variable "admin_role_arns" {
  description = "Data lake administrators (keep existing admins in this list, the settings resource replaces it)"
  type        = list(string)
}

variable "role_arns" {
  description = "Role ARNs keyed by persona"
  type = object({
    data_engineer      = string
    risk_analyst       = string
    compliance_auditor = string
    branch_ops_south   = string
  })
}

data "aws_caller_identity" "current" {}

locals {
  database = "lending_curated"
  customer_columns = [
    { name = "customer_id", type = "string" },
    { name = "full_name", type = "string" },
    { name = "pan_number", type = "string" },
    { name = "mobile", type = "string" },
    { name = "city", type = "string" },
    { name = "branch_code", type = "string" },
    { name = "region", type = "string" },
    { name = "risk_tier", type = "string" },
    { name = "kyc_status", type = "string" },
  ]
}

# --- settings: admins, and no IAMAllowedPrincipals grant on new databases/tables ---
resource "aws_lakeformation_data_lake_settings" "this" {
  admins = var.admin_role_arns
  # empty blocks = Lake Formation permissions only for newly created resources
}

resource "aws_lakeformation_resource" "curated" {
  arn = var.lake_bucket_arn
  # uses the Lake Formation service-linked role to vend credentials to Athena/Glue
}

# --- catalog ---
resource "aws_glue_catalog_database" "lending" {
  name = local.database
}

resource "aws_glue_catalog_table" "customers" {
  database_name = aws_glue_catalog_database.lending.name
  name          = "customers"
  table_type    = "EXTERNAL_TABLE"
  parameters    = { classification = "parquet" }

  storage_descriptor {
    location      = "s3://${split(":::", var.lake_bucket_arn)[1]}/curated/customers/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }
    dynamic "columns" {
      for_each = local.customer_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

# --- LF-tags ---
resource "aws_lakeformation_lf_tag" "domain" {
  key    = "domain"
  values = ["lending", "cards"]
}

resource "aws_lakeformation_lf_tag" "sensitivity" {
  key    = "sensitivity"
  values = ["public", "internal", "pii"]
}

resource "aws_lakeformation_resource_lf_tags" "database" {
  database {
    name = aws_glue_catalog_database.lending.name
  }
  lf_tag {
    key   = aws_lakeformation_lf_tag.domain.key
    value = "lending"
  }
  lf_tag {
    key   = aws_lakeformation_lf_tag.sensitivity.key
    value = "internal"
  }
}

resource "aws_lakeformation_resource_lf_tags" "customers_pii" {
  table_with_columns {
    database_name = aws_glue_catalog_database.lending.name
    name          = aws_glue_catalog_table.customers.name
    column_names  = ["full_name", "pan_number", "mobile"]
  }
  lf_tag {
    key   = aws_lakeformation_lf_tag.sensitivity.key
    value = "pii"
  }
}

resource "aws_lakeformation_resource_lf_tags" "customers_public" {
  table_with_columns {
    database_name = aws_glue_catalog_database.lending.name
    name          = aws_glue_catalog_table.customers.name
    column_names  = ["city", "region"]
  }
  lf_tag {
    key   = aws_lakeformation_lf_tag.sensitivity.key
    value = "public"
  }
}

# --- tag-based grants ---
locals {
  tag_grants = {
    data_engineer      = { sensitivity = ["public", "internal", "pii"], permissions = ["SELECT", "DESCRIBE", "ALTER", "INSERT"] }
    risk_analyst       = { sensitivity = ["public", "internal"], permissions = ["SELECT", "DESCRIBE"] }
    compliance_auditor = { sensitivity = ["public", "internal", "pii"], permissions = ["SELECT", "DESCRIBE"] }
  }
}

resource "aws_lakeformation_permissions" "database_describe" {
  for_each    = local.tag_grants
  principal   = var.role_arns[each.key]
  permissions = ["DESCRIBE"]

  lf_tag_policy {
    resource_type = "DATABASE"
    expression {
      key    = aws_lakeformation_lf_tag.domain.key
      values = ["lending"]
    }
  }
}

resource "aws_lakeformation_permissions" "tables" {
  for_each    = local.tag_grants
  principal   = var.role_arns[each.key]
  permissions = each.value.permissions

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = aws_lakeformation_lf_tag.domain.key
      values = ["lending"]
    }
    expression {
      key    = aws_lakeformation_lf_tag.sensitivity.key
      values = each.value.sensitivity
    }
  }
}

# --- row and column filter for the South branch operations team ---
resource "aws_lakeformation_data_cells_filter" "customers_south_no_pii" {
  table_data {
    database_name    = aws_glue_catalog_database.lending.name
    table_name       = aws_glue_catalog_table.customers.name
    table_catalog_id = data.aws_caller_identity.current.account_id
    name             = "customers_south_no_pii"

    column_wildcard {
      excluded_column_names = ["full_name", "pan_number", "mobile"]
    }
    row_filter {
      filter_expression = "region = 'SOUTH'"
    }
  }
}

resource "aws_lakeformation_permissions" "branch_ops_south" {
  principal   = var.role_arns.branch_ops_south
  permissions = ["SELECT"]

  data_cells_filter {
    database_name    = aws_glue_catalog_database.lending.name
    table_name       = aws_glue_catalog_table.customers.name
    table_catalog_id = data.aws_caller_identity.current.account_id
    name             = aws_lakeformation_data_cells_filter.customers_south_no_pii.table_data[0].name
  }
}
