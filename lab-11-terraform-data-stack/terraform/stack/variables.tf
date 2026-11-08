variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-south-1"
}

variable "org" {
  description = "Short org prefix used in bucket names"
  type        = string
}

variable "domain" {
  description = "Data domain this stack serves, e.g. lending"
  type        = string
}

variable "environment" {
  description = "dev or prod"
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "owner" {
  description = "Team that owns the stack (tag)"
  type        = string
}

variable "cost_center" {
  description = "Cost center for chargeback (tag)"
  type        = string
}

variable "data_classification" {
  description = "Highest classification of data in the stack: public, internal, confidential, restricted"
  type        = string
  validation {
    condition     = contains(["public", "internal", "confidential", "restricted"], var.data_classification)
    error_message = "unknown data_classification."
  }
}

variable "glue_workers" {
  description = "Workers for the curate job"
  type        = number
  default     = 2
}

variable "raw_retention_days" {
  description = "Expire raw objects after this many days (0 keeps them)"
  type        = number
  default     = 0
}
