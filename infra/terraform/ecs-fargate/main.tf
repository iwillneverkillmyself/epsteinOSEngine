data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  name_prefix = var.project_name
  ecr_repo    = "${var.project_name}"
  image_uri   = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
}

# ---------------------------
# S3 bucket for assets
# ---------------------------
resource "aws_s3_bucket" "assets" {
  bucket = var.s3_bucket_name
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------
# ECR repository
# ---------------------------
resource "aws_ecr_repository" "api" {
  name                 = local.ecr_repo
  image_tag_mutability = "MUTABLE"
}

# ---------------------------
# CloudWatch logs
# ---------------------------
resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name_prefix}-api"
  retention_in_days = 14
}

# ---------------------------
# IAM roles
# ---------------------------
resource "aws_iam_role" "task_execution" {
  name = "${local.name_prefix}-ecsTaskExecutionRole"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution_attach" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name = "${local.name_prefix}-taskRole"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "task_policy" {
  name = "${local.name_prefix}-taskPolicy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3AssetsReadWrite"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.assets.arn}/*"
        ]
      },
      {
        Sid    = "Textract"
        Effect = "Allow"
        Action = ["textract:AnalyzeDocument", "textract:DetectDocumentText"]
        Resource = ["*"]
      },
      {
        Sid    = "Rekognition"
        Effect = "Allow"
        Action = ["rekognition:DetectLabels", "rekognition:RecognizeCelebrities"]
        Resource = ["*"]
      },
      {
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream"
        ]
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "task_policy_attach" {
  role       = aws_iam_role.task.name
  policy_arn = aws_iam_policy.task_policy.arn
}

# Store DATABASE_URL in SSM so it doesn't live in the task definition JSON directly.
resource "aws_ssm_parameter" "database_url" {
  name  = "/${local.name_prefix}/DATABASE_URL"
  type  = "SecureString"
  value = var.database_url
}

# Secret for anonymous comment username signatures
resource "random_password" "comments_hmac_secret" {
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "comments_hmac_secret" {
  name  = "/${local.name_prefix}/COMMENTS_HMAC_SECRET"
  type  = "SecureString"
  value = random_password.comments_hmac_secret.result
}

# Chat API key for EpsteinGPT
resource "random_password" "chat_api_key" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "chat_api_key" {
  name  = "/${local.name_prefix}/CHAT_API_KEY"
  type  = "SecureString"
  value = random_password.chat_api_key.result
}

# Master API key for all endpoints (restricts public access)
resource "random_password" "master_api_key" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "master_api_key" {
  name  = "/${local.name_prefix}/MASTER_API_KEY"
  type  = "SecureString"
  value = random_password.master_api_key.result
}

# Allow ECS execution role to read our SSM parameter (and decrypt SecureString).
resource "aws_iam_role_policy" "task_execution_ssm" {
  name = "${local.name_prefix}-execution-ssm"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ssm:GetParameters", "ssm:GetParameter"]
        Resource = [
          aws_ssm_parameter.database_url.arn,
          aws_ssm_parameter.comments_hmac_secret.arn,
          aws_ssm_parameter.chat_api_key.arn,
          aws_ssm_parameter.master_api_key.arn
        ]
      },
      {
        Effect = "Allow"
        Action = ["kms:Decrypt"]
        Resource = ["*"]
      }
    ]
  })
}

# ---------------------------
# Networking: ALB + SGs
# ---------------------------
resource "aws_security_group" "alb" {
  name   = "${local.name_prefix}-alb-sg"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ecs_tasks" {
  name   = "${local.name_prefix}-ecs-tasks-sg"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Allow ECS tasks to reach RDS by opening RDS SG ingress from ECS SG
resource "aws_security_group_rule" "rds_from_ecs" {
  type                     = "ingress"
  security_group_id        = var.rds_security_group_id
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs_tasks.id
  description              = "Allow Postgres from ECS tasks"
}

resource "aws_lb" "api" {
  name               = "${local.name_prefix}-alb"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name_prefix}-tg"
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.default.id

  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# ---------------------------
# ECS cluster + task + service
# ---------------------------
resource "aws_ecs_cluster" "api" {
  name = "${local.name_prefix}-cluster"
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = local.image_uri
      essential = true
      portMappings = [
        { containerPort = var.container_port, hostPort = var.container_port, protocol = "tcp" }
      ]
      environment = [
        { name = "PORT", value = tostring(var.container_port) },
        { name = "LOG_LEVEL", value = var.log_level },
        { name = "OCR_ENGINE", value = var.ocr_engine },
        { name = "S3_BUCKET", value = aws_s3_bucket.assets.bucket },
        { name = "S3_REGION", value = var.aws_region },
        { name = "S3_FILES_PREFIX", value = var.s3_files_prefix },
        { name = "S3_IMAGES_PREFIX", value = var.s3_images_prefix },
        { name = "COMMENTS_RATE_LIMIT_PER_MINUTE", value = tostring(var.comments_rate_limit_per_minute) }
      ]
      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
        { name = "COMMENTS_HMAC_SECRET", valueFrom = aws_ssm_parameter.comments_hmac_secret.arn },
        { name = "CHAT_API_KEY", valueFrom = aws_ssm_parameter.chat_api_key.arn },
        { name = "MASTER_API_KEY", valueFrom = aws_ssm_parameter.master_api_key.arn }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name_prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = local.image_uri
      essential = true
      command   = ["python", "-m", "summaries.worker_service"]
      environment = [
        { name = "LOG_LEVEL", value = var.log_level },
        { name = "S3_BUCKET", value = aws_s3_bucket.assets.bucket },
        { name = "S3_REGION", value = var.aws_region },
        { name = "S3_FILES_PREFIX", value = var.s3_files_prefix },
        { name = "S3_IMAGES_PREFIX", value = var.s3_images_prefix },
        { name = "SUMMARIES_WORKER_BATCH_SIZE", value = "1" },
        { name = "SUMMARIES_WORKER_POLL_SECONDS", value = "10" }
      ]
      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
        { name = "COMMENTS_HMAC_SECRET", valueFrom = aws_ssm_parameter.comments_hmac_secret.arn }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "doj_ingester" {
  family                   = "${local.name_prefix}-doj-ingester"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "doj_ingester"
      image     = local.image_uri
      essential = true
      command   = ["python", "-m", "ingestion.doj_service"]
      environment = [
        { name = "LOG_LEVEL", value = var.log_level },
        { name = "S3_BUCKET", value = aws_s3_bucket.assets.bucket },
        { name = "S3_REGION", value = var.aws_region },
        { name = "S3_FILES_PREFIX", value = var.s3_files_prefix },
        { name = "S3_IMAGES_PREFIX", value = var.s3_images_prefix },
        { name = "DOJ_SKIP_EXISTING", value = "true" },
        { name = "DOJ_INGEST_POLL_SECONDS", value = "60" },
        { name = "DOJ_INGEST_RUN_INTERVAL_SECONDS", value = "600" }
      ]
      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
        { name = "COMMENTS_HMAC_SECRET", valueFrom = aws_ssm_parameter.comments_hmac_secret.arn }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "doj"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${local.name_prefix}-service"
  cluster         = aws_ecs_cluster.api.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name_prefix}-worker-service"
  cluster         = aws_ecs_cluster.api.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }
}

resource "aws_ecs_service" "doj_ingester" {
  name            = "${local.name_prefix}-doj-ingester-service"
  cluster         = aws_ecs_cluster.api.id
  task_definition = aws_ecs_task_definition.doj_ingester.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }
}



