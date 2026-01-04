output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  value = var.aws_region
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "assets_bucket" {
  value = aws_s3_bucket.assets.bucket
}

output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.api.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "comments_hmac_secret_ssm_param" {
  value = aws_ssm_parameter.comments_hmac_secret.name
}

output "master_api_key_ssm_param" {
  value       = aws_ssm_parameter.master_api_key.name
  description = "SSM param name for the master API key (value is stored as SecureString)."
}

output "chat_api_key_ssm_param" {
  value       = aws_ssm_parameter.chat_api_key.name
  description = "SSM param name for the chat API key (value is stored as SecureString)."
}




