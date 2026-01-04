# Optional: IAM role for GitHub Actions (OIDC) to push to ECR and trigger ECS redeploys.
#
# This expects you already have an IAM OIDC provider created for GitHub:
#   url: https://token.actions.githubusercontent.com
# If you already created it in your account, pass its ARN via github_oidc_provider_arn.

variable "github_oidc_provider_arn" {
  type        = string
  description = "ARN of the existing IAM OIDC provider for GitHub Actions (token.actions.githubusercontent.com)."
  default     = ""
}

variable "github_owner" {
  type        = string
  description = "GitHub org/user that owns the repository."
  default     = "iwillneverkillmyself"
}

variable "github_repo" {
  type        = string
  description = "GitHub repository name."
  default     = "epsteinOSEngine"
}

variable "github_branch" {
  type        = string
  description = "Branch that is allowed to assume the deploy role."
  default     = "main"
}

locals {
  enable_github_actions_role = var.github_oidc_provider_arn != ""
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  count = local.enable_github_actions_role ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/${var.github_branch}"]
    }
  }
}

resource "aws_iam_role" "github_actions_deploy" {
  count = local.enable_github_actions_role ? 1 : 0

  name               = "${var.project_name}-github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role[0].json
}

data "aws_iam_policy_document" "github_actions_deploy" {
  count = local.enable_github_actions_role ? 1 : 0

  statement {
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload"
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  # GitHub Actions forces a new deployment so ECS pulls the freshly pushed :latest tag.
  statement {
    effect = "Allow"
    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
      "ecs:ListTasks",
      "ecs:DescribeTasks"
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  count = local.enable_github_actions_role ? 1 : 0

  name   = "${var.project_name}-github-actions-deploy"
  role   = aws_iam_role.github_actions_deploy[0].id
  policy = data.aws_iam_policy_document.github_actions_deploy[0].json
}

output "github_actions_deploy_role_arn" {
  description = "Set this as GitHub Actions secret AWS_ROLE_TO_ASSUME."
  value       = local.enable_github_actions_role ? aws_iam_role.github_actions_deploy[0].arn : null
}


