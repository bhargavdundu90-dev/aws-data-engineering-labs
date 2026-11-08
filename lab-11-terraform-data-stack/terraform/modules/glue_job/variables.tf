variable "name" {
  description = "Glue job name"
  type        = string
}

variable "script_s3_path" {
  description = "s3:// path of the job script"
  type        = string
}

variable "read_bucket_arns" {
  description = "Buckets the job reads from"
  type        = list(string)
}

variable "write_bucket_arn" {
  description = "Bucket the job writes to"
  type        = string
}

variable "write_prefix" {
  description = "Prefix inside the write bucket the job may write under (no leading slash)"
  type        = string
}

variable "glue_database_names" {
  description = "Glue databases the job may read and update tables in"
  type        = list(string)
}

variable "kms_key_arn" {
  description = "KMS key of the lake buckets"
  type        = string
}

variable "worker_type" {
  description = "G.1X or G.2X"
  type        = string
  default     = "G.1X"
}

variable "number_of_workers" {
  description = "Worker count; the per-environment cap is enforced by tfguard on the plan"
  type        = number
  default     = 2
}

variable "timeout_minutes" {
  description = "Job timeout. Glue's default is 48 hours, which hides stuck jobs."
  type        = number
  default     = 60
}

variable "default_arguments" {
  description = "Extra --arguments for the job"
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Tags for the job and its role"
  type        = map(string)
  default     = {}
}
