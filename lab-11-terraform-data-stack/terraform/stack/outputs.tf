output "raw_bucket" {
  description = "Raw zone bucket"
  value       = module.raw.bucket
}

output "curated_bucket" {
  description = "Curated zone bucket"
  value       = module.curated.bucket
}

output "glue_databases" {
  description = "Glue databases created by the stack"
  value       = [module.raw_db.name, module.curated_db.name]
}

output "curate_job" {
  description = "Glue job name"
  value       = module.curate_job.job_name
}

output "athena_workgroup" {
  description = "Athena workgroup for analysts"
  value       = aws_athena_workgroup.analysts.name
}
