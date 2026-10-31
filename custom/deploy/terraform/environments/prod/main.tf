terraform {
  required_version = "= 1.14.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.20.0" }
  }
}
terraform {
  backend "s3" {}
}
variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Supply the expected AWS account ID."
  }
}
variable "domain_name" { type = string }
variable "hosted_zone_id" { type = string }
variable "region" { type = string }
variable "template_url" { type = string }
variable "cloudformation_role_arn" { type = string }
variable "parameters" { type = map(string) }
variable "release_digest" { type = string }
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
}
module "application" {
  source                  = "../../modules/application"
  domain_name             = var.domain_name
  hosted_zone_id          = var.hosted_zone_id
  environment             = "prod"
  template_url            = var.template_url
  cloudformation_role_arn = var.cloudformation_role_arn
  parameters              = var.parameters
  release_digest          = var.release_digest
}
output "stack_outputs" { value = module.application.outputs }
output "stack_id" { value = module.application.stack_id }
