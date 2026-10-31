terraform {
  required_providers { aws = { source = "hashicorp/aws" } }
}
variable "environment" { type = string }
variable "template_url" { type = string }
variable "cloudformation_role_arn" { type = string }
variable "parameters" { type = map(string) }
variable "release_digest" { type = string }
resource "aws_cloudformation_stack" "application" {
  name               = "kirocrew-${var.environment}"
  template_url       = var.template_url
  iam_role_arn       = var.cloudformation_role_arn
  capabilities       = ["CAPABILITY_IAM", "CAPABILITY_AUTO_EXPAND"]
  parameters         = var.parameters
  timeout_in_minutes = 45
  tags               = { Environment = var.environment, Application = "kirocrew", ReleaseDigest = var.release_digest }
  lifecycle { prevent_destroy = true }
}
output "outputs" { value = aws_cloudformation_stack.application.outputs }
output "stack_id" { value = aws_cloudformation_stack.application.id }

variable "domain_name" { type = string }
variable "hosted_zone_id" { type = string }
resource "aws_acm_certificate" "web" {
  domain_name       = var.domain_name
  validation_method = "DNS"
  tags              = { Application = "kirocrew", Environment = var.environment }
  lifecycle { create_before_destroy = true }
}
resource "aws_route53_record" "validation" {
  zone_id = var.hosted_zone_id
  name    = one(aws_acm_certificate.web.domain_validation_options).resource_record_name
  type    = one(aws_acm_certificate.web.domain_validation_options).resource_record_type
  records = [one(aws_acm_certificate.web.domain_validation_options).resource_record_value]
  ttl     = 60
}
resource "aws_acm_certificate_validation" "web" {
  certificate_arn         = aws_acm_certificate.web.arn
  validation_record_fqdns = [aws_route53_record.validation.fqdn]
}
resource "aws_apigatewayv2_domain_name" "web" {
  domain_name = var.domain_name
  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.web.certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}
resource "aws_apigatewayv2_api_mapping" "web" {
  api_id      = aws_cloudformation_stack.application.outputs["HttpApiId"]
  domain_name = aws_apigatewayv2_domain_name.web.id
  stage       = "$default"
}
resource "aws_route53_record" "web" {
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"
  alias {
    name                   = aws_apigatewayv2_domain_name.web.domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.web.domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}
