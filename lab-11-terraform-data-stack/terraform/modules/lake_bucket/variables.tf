variable "name" {
  description = "Full bucket name, e.g. acme-lending-dev-raw"
  type        = string
}

variable "zone" {
  description = "Lake zone this bucket holds: raw, curated or athena-results"
  type        = string
  validation {
    condition     = contains(["raw", "curated", "athena-results"], var.zone)
    error_message = "zone must be raw, curated or athena-results."
  }
}

variable "kms_key_arn" {
  description = "KMS key used for SSE-KMS on every object"
  type        = string
}

variable "noncurrent_days" {
  description = "Days to keep noncurrent object versions before expiry"
  type        = number
  default     = 30
}

variable "ia_after_days" {
  description = "Move current objects to STANDARD_IA after this many days (0 disables the transition)"
  type        = number
  default     = 0
}

variable "expire_after_days" {
  description = "Expire current objects after this many days (0 keeps them forever)"
  type        = number
  default     = 0
}

variable "force_destroy" {
  description = "Allow terraform destroy to empty the bucket. Only for throwaway dev stacks."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags merged onto the bucket"
  type        = map(string)
  default     = {}
}
