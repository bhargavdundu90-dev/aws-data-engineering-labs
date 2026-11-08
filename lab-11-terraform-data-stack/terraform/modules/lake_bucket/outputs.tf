output "bucket" {
  description = "Bucket name"
  value       = aws_s3_bucket.this.id
}

output "arn" {
  description = "Bucket ARN"
  value       = aws_s3_bucket.this.arn
}
