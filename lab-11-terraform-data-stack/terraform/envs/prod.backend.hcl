bucket         = "acme-terraform-state-prod"
key            = "data-stacks/lending/terraform.tfstate"
region         = "ap-south-1"
dynamodb_table = "acme-terraform-locks"
encrypt        = true
