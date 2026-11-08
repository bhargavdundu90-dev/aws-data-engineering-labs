output "job_name" {
  description = "Glue job name"
  value       = aws_glue_job.this.name
}

output "role_arn" {
  description = "IAM role the job runs as"
  value       = aws_iam_role.job.arn
}
