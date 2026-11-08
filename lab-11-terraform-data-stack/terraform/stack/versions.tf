terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # partial config: bucket/key/table come from envs/<env>.backend.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = local.tags
  }
}
