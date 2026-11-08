output "name" {
  description = "Glue database name"
  value       = aws_glue_catalog_database.this.name
}

output "arn" {
  description = "Glue database ARN"
  value       = aws_glue_catalog_database.this.arn
}
