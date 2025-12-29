## ECS Fargate (Terraform)

This Terraform stack provisions:
- ECR repo
- ECS cluster + Fargate service
- Internet-facing ALB (HTTP 80) forwarding to the service
- CloudWatch logs
- S3 bucket for assets (PDFs + page images)
- IAM roles (execution + task role)
- A security-group rule to allow **RDS Postgres** inbound from ECS tasks

### Prereqs
- AWS CLI configured (`aws configure`)
- Terraform installed
- Docker installed + running
- You already have RDS Postgres created (we only need its **Security Group ID** and a `DATABASE_URL`)

### 1) Create a terraform.tfvars

```hcl
aws_region          = "us-east-1"
project_name        = "epsteingptengine"
image_tag           = "latest"
s3_bucket_name      = "epsteingptengine-assets-CHANGE-ME"
rds_security_group_id = "sg-xxxxxxxxxxxxxxxxx"

# IMPORTANT: put your real URL here
database_url = "postgresql+psycopg2://postgres:YOUR_PASSWORD@database-1.ccxoasco2m9l.us-east-1.rds.amazonaws.com:5432/epsteingptengine?sslmode=require"
```

### 2) Apply

```bash
cd infra/terraform/ecs-fargate
terraform init
terraform apply
```

### 3) Push the image to ECR (AWS CLI)

After `apply`, Terraform outputs `ecr_repository_url`. Then:

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPO=epsteingptengine
export IMAGE_TAG=latest

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
docker build -t "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:$IMAGE_TAG" .
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:$IMAGE_TAG"
```

Then re-run:

```bash
cd infra/terraform/ecs-fargate
terraform apply
```

### 4) Upload assets to S3

```bash
python scripts/sync_assets_to_s3.py --bucket YOUR_BUCKET --region us-east-1
```

### 5) Test
Terraform outputs `alb_dns_name`:

```bash
curl "http://ALB_DNS_NAME/health"
```




