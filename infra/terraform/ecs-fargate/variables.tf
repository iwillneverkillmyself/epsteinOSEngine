variable "aws_region" {
  type        = string
  description = "AWS region"
  default     = "us-east-1"
}

variable "project_name" {
  type        = string
  description = "Prefix for created resources"
  default     = "epsteingptengine"
}

variable "image_tag" {
  type        = string
  description = "ECR image tag to deploy"
  default     = "latest"
}

variable "container_port" {
  type        = number
  description = "Container port exposed by the API"
  default     = 8000
}

variable "cpu" {
  type        = number
  description = "Fargate CPU units"
  default     = 1024
}

variable "memory" {
  type        = number
  description = "Fargate memory (MiB)"
  default     = 2048
}

variable "desired_count" {
  type        = number
  description = "Number of tasks"
  default     = 1
}

variable "worker_desired_count" {
  type        = number
  description = "Number of worker tasks (summaries/tags backfill)"
  default     = 1
}

variable "comments_rate_limit_per_minute" {
  type        = number
  description = "Rate limit for anonymous comment write endpoints per IP per minute"
  default     = 20
}

variable "database_url" {
  type        = string
  description = "SQLAlchemy DATABASE_URL (Postgres). Stored in SSM SecureString."
  sensitive   = true
}

variable "s3_bucket_name" {
  type        = string
  description = "S3 bucket for assets (files/images). Must be globally unique."
}

variable "rds_security_group_id" {
  type        = string
  description = "Existing RDS security group id to allow inbound 5432 from ECS tasks"
}

variable "log_level" {
  type        = string
  description = "LOG_LEVEL env var"
  default     = "INFO"
}

variable "ocr_engine" {
  type        = string
  description = "OCR_ENGINE env var"
  default     = "textract"
}

variable "s3_files_prefix" {
  type        = string
  description = "S3 prefix for original files"
  default     = "files"
}

variable "s3_images_prefix" {
  type        = string
  description = "S3 prefix for images"
  default     = "images"
}




