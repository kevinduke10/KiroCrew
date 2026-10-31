variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Supply the expected AWS account ID."
  }
}
variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "Use dev, test or prod."
  }
}
variable "hosted_zone_id" { type = string }
variable "domain_name" { type = string }
variable "region" { type = string }
variable "repository" {
  type = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.repository))
    error_message = "Use an exact GitHub owner/repository."
  }
}
variable "existing_oidc_provider_arn" {
  type        = string
  default     = null
  description = "Import/reuse an existing GitHub provider instead of creating a duplicate."
}
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags { tags = { Application = "kirocrew", Environment = var.environment } }
}
locals {
  name      = "kc-${var.environment}-${var.account_id}"
  stack     = "kirocrew-${var.environment}"
  arn       = "arn:aws"
  stack_arn = "${local.arn}:cloudformation:${var.region}:${var.account_id}:stack/${local.stack}/*"
  roles     = "${local.arn}:iam::${var.account_id}:role/${local.stack}-*"
}
resource "aws_s3_bucket" "storage" {
  for_each      = toset(["state", "artifacts"])
  bucket        = "${local.name}-${each.key}"
  force_destroy = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "storage" {
  for_each = aws_s3_bucket.storage
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "storage" {
  for_each = aws_s3_bucket.storage
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_public_access_block" "storage" {
  for_each                = aws_s3_bucket.storage
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_policy" "storage" {
  for_each = aws_s3_bucket.storage
  bucket   = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [each.value.arn, "${each.value.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }, {
    Effect    = "Deny", Principal = "*", Action = "s3:PutObject",
    Resource  = "${each.value.arn}/releases/*",
    Condition = { Null = { "s3:if-none-match" = "true" } }
    }, {
    Effect   = "Deny", Principal = "*", Action = ["s3:DeleteObject", "s3:DeleteObjectVersion"],
    Resource = "${each.value.arn}/releases/*"
  }] })
}
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.existing_oidc_provider_arn == null ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
locals {
  oidc = var.existing_oidc_provider_arn == null ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn
}
resource "aws_iam_role" "ci" {
  for_each = toset(["plan", "apply"])
  name     = "${local.name}-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Federated = local.oidc }, Action = "sts:AssumeRoleWithWebIdentity",
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = "repo:${var.repository}:environment:${var.environment}-${each.key}"
    } }
  }] })
}
resource "aws_iam_role" "cloudformation" {
  name = "${local.name}-cloudformation"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "cloudformation.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
# Application roles cannot acquire IAM, STS, state-bucket or deployment authority.
resource "aws_iam_policy" "runtime_boundary" {
  name = "${local.name}-runtime-boundary"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["dynamodb:*"], Resource = ["${local.arn}:dynamodb:${var.region}:${var.account_id}:table/${local.stack}-*"] },
    { Effect = "Allow", Action = ["s3:*"], Resource = ["${local.arn}:s3:::${local.stack}-*", "${local.arn}:s3:::${local.stack}-*/*"] },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = ["${local.arn}:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.stack}-*", "${local.arn}:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.stack}-*:*"] },
    { Effect = "Allow", Action = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminGetUser", "cognito-idp:GetUser"], Resource = ["${local.arn}:cognito-idp:${var.region}:${var.account_id}:userpool/*"] },
    { Effect = "Allow", Action = ["bedrock:Retrieve", "bedrock:IngestKnowledgeBaseDocuments", "bedrock:GetKnowledgeBaseDocuments"], Resource = ["${local.arn}:bedrock:${var.region}:${var.account_id}:knowledge-base/*"] },
    { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = ["${local.arn}:bedrock:${var.region}::foundation-model/*"] },
    { Effect = "Allow", Action = ["s3vectors:GetIndex", "s3vectors:QueryVectors", "s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors", "s3vectors:ListVectors"], Resource = ["${local.arn}:s3vectors:${var.region}:${var.account_id}:bucket/${local.stack}-*"] }
  ] })
}
resource "aws_iam_role_policy" "ci" {
  for_each = aws_iam_role.ci
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["acm:DescribeCertificate", "acm:ListTagsForCertificate"], Resource = ["arn:aws:acm:${var.region}:${var.account_id}:certificate/*"] },
    { Effect = "Allow", Action = ["route53:GetHostedZone", "route53:ListResourceRecordSets"], Resource = ["arn:aws:route53:::hostedzone/${var.hosted_zone_id}"] },
    { Effect = "Allow", Action = ["route53:GetChange"], Resource = ["arn:aws:route53:::change/*"] },
    { Effect = "Allow", Action = ["apigateway:GET"], Resource = ["arn:aws:apigateway:${var.region}::/domainnames/${var.domain_name}", "arn:aws:apigateway:${var.region}::/domainnames/${var.domain_name}/*"] },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.storage["state"].arn, aws_s3_bucket.storage["artifacts"].arn] },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.storage["state"].arn}/application/terraform.tfstate"] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.storage["state"].arn}/application/terraform.tfstate.tflock"] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"], Resource = ["${aws_s3_bucket.storage["artifacts"].arn}/releases/*"] },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.storage["artifacts"].arn}/deployments/current.json"] },
    { Effect = "Allow", Action = ["cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents", "cloudformation:GetTemplate", "cloudformation:GetStackPolicy", "cloudformation:ListStackResources", "cloudformation:DetectStackDrift", "cloudformation:DescribeStackResourceDrifts"], Resource = [local.stack_arn] },
    { Effect = "Allow", Action = ["cloudformation:DescribeStackDriftDetectionStatus", "cloudformation:ValidateTemplate"], Resource = ["*"] }
    ], [for statement in [
      { Effect = "Allow", Action = ["iam:CreateServiceLinkedRole"], Resource = ["arn:aws:iam::${var.account_id}:role/aws-service-role/ops.apigateway.amazonaws.com/AWSServiceRoleForAPIGateway"], Condition = { StringEquals = { "iam:AWSServiceName" = "ops.apigateway.amazonaws.com" } } },
      { Effect = "Allow", Action = ["acm:RequestCertificate", "acm:AddTagsToCertificate", "acm:RemoveTagsFromCertificate", "acm:DeleteCertificate"], Resource = ["arn:aws:acm:${var.region}:${var.account_id}:certificate/*"] },
      { Effect = "Allow", Action = ["route53:ChangeResourceRecordSets"], Resource = ["arn:aws:route53:::hostedzone/${var.hosted_zone_id}"] },
      { Effect = "Allow", Action = ["apigateway:POST", "apigateway:PATCH", "apigateway:DELETE"], Resource = ["arn:aws:apigateway:${var.region}::/domainnames", "arn:aws:apigateway:${var.region}::/domainnames/${var.domain_name}", "arn:aws:apigateway:${var.region}::/domainnames/${var.domain_name}/*"] },
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.storage["artifacts"].arn}/deployments/current.json"] },
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.storage["state"].arn}/application/terraform.tfstate"] },
      { Effect = "Allow", Action = ["cloudformation:CreateStack", "cloudformation:UpdateStack", "cloudformation:UpdateTerminationProtection"], Resource = [local.stack_arn] },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.cloudformation.arn], Condition = { StringEquals = { "iam:PassedToService" = "cloudformation.amazonaws.com" } } }
  ] : statement if each.key == "apply"]) })
}
resource "aws_iam_role_policy" "cloudformation" {
  role = aws_iam_role.cloudformation.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["lambda:*"], Resource = ["${local.arn}:lambda:${var.region}:${var.account_id}:function:${local.stack}-*"] },
    { Effect = "Allow", Action = ["dynamodb:*"], Resource = ["${local.arn}:dynamodb:${var.region}:${var.account_id}:table/${local.stack}-*"] },
    { Effect = "Allow", Action = ["s3:*"], Resource = ["${local.arn}:s3:::${local.stack}-*", "${local.arn}:s3:::${local.stack}-*/*"] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = ["${aws_s3_bucket.storage["artifacts"].arn}/releases/*"] },
    { Effect = "Allow", Action = ["apigateway:*"], Resource = ["${local.arn}:apigateway:${var.region}::*"] },
    { Effect = "Allow", Action = ["cognito-idp:CreateUserPool", "cognito-idp:DescribeUserPool", "cognito-idp:UpdateUserPool", "cognito-idp:DeleteUserPool", "cognito-idp:CreateUserPoolClient", "cognito-idp:DescribeUserPoolClient", "cognito-idp:UpdateUserPoolClient", "cognito-idp:DeleteUserPoolClient", "cognito-idp:CreateUserPoolDomain", "cognito-idp:DescribeUserPoolDomain", "cognito-idp:UpdateUserPoolDomain", "cognito-idp:DeleteUserPoolDomain", "cognito-idp:TagResource", "cognito-idp:UntagResource", "cognito-idp:ListTagsForResource"], Resource = ["*"] },
    { Effect = "Allow", Action = ["bedrock:CreateKnowledgeBase", "bedrock:GetKnowledgeBase", "bedrock:UpdateKnowledgeBase", "bedrock:DeleteKnowledgeBase", "bedrock:CreateDataSource", "bedrock:GetDataSource", "bedrock:UpdateDataSource", "bedrock:DeleteDataSource", "bedrock:TagResource", "bedrock:UntagResource", "bedrock:ListTagsForResource"], Resource = ["${local.arn}:bedrock:${var.region}:${var.account_id}:knowledge-base/*"] },
    { Effect = "Allow", Action = ["s3vectors:*"], Resource = ["${local.arn}:s3vectors:${var.region}:${var.account_id}:bucket/${local.stack}-*"] },
    { Effect = "Allow", Action = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"], Resource = [local.roles], Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.runtime_boundary.arn } } },
    { Effect = "Allow", Action = ["iam:GetRole", "iam:DeleteRole", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:TagRole", "iam:UntagRole"], Resource = [local.roles] },
    { Effect = "Allow", Action = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"], Resource = [local.roles], Condition = { ArnEquals = { "iam:PolicyARN" = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" } } },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [local.roles], Condition = { StringEquals = { "iam:PassedToService" = ["lambda.amazonaws.com", "bedrock.amazonaws.com"] } } }
  ] })
}
output "configuration" {
  value = {
    account_id              = var.account_id, region = var.region,
    state_bucket            = aws_s3_bucket.storage["state"].id,
    artifact_bucket         = aws_s3_bucket.storage["artifacts"].id,
    plan_role_arn           = aws_iam_role.ci["plan"].arn,
    apply_role_arn          = aws_iam_role.ci["apply"].arn,
    cloudformation_role_arn = aws_iam_role.cloudformation.arn,
    runtime_boundary_arn    = aws_iam_policy.runtime_boundary.arn
  }
}
