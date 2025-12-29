#!/usr/bin/env bash
set -euo pipefail

# Build and push the API image to ECR.
#
# Prereqs:
# - AWS CLI configured: aws configure
# - Docker running
#
# Usage:
#   export AWS_REGION=us-east-1
#   export AWS_ACCOUNT_ID=123456789012
#   export ECR_REPO=epsteingptengine
#   export IMAGE_TAG=$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
#   ./deploy/ecs/build_and_push_ecr.sh

: "${AWS_REGION:?set AWS_REGION}"
: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}"
: "${ECR_REPO:?set ECR_REPO}"
: "${IMAGE_TAG:?set IMAGE_TAG}"

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${ECR_URI}/${ECR_REPO}:${IMAGE_TAG}"

aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${AWS_REGION}" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "${ECR_REPO}" --region "${AWS_REGION}" >/dev/null

aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${ECR_URI}"

docker build -t "${IMAGE}" .
docker push "${IMAGE}"

echo "Pushed: ${IMAGE}"




