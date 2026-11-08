variable "database_name" {
  description = "Glue database name"
  type        = string
}

variable "description" {
  description = "Database description shown in the Glue console"
  type        = string
  default     = ""
}

variable "location" {
  description = "Default S3 location for tables in this database (s3://bucket/prefix)"
  type        = string
}

variable "tags" {
  description = "Tags for the database"
  type        = map(string)
  default     = {}
}
