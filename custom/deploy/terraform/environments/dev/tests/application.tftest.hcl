mock_provider "aws" {
  mock_resource "aws_cloudformation_stack" {
    defaults = { outputs = { HttpApiId = "example", ApiUrl = "https://example.execute-api.us-east-1.amazonaws.com" } }
  }
  mock_resource "aws_acm_certificate" {
    defaults = {
      arn                       = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
      domain_validation_options = [{ domain_name = "dev.example.com", resource_record_name = "_validation.dev.example.com", resource_record_type = "CNAME", resource_record_value = "_validation.acm-validations.aws." }]
    }
  }
}
variables {
  account_id              = "123456789012"
  region                  = "us-east-1"
  domain_name             = "dev.example.com"
  hosted_zone_id          = "ZEXAMPLE"
  template_url            = "https://example.s3.us-east-1.amazonaws.com/template.json"
  cloudformation_role_arn = "arn:aws:iam::123456789012:role/cfn"
  parameters              = { ManageKnowledge = "true" }
  release_digest          = "test-digest"
}
run "shared_application" {
  command = apply
  assert {
    condition     = output.stack_outputs["HttpApiId"] == "example"
    error_message = "The shared stack must expose the API for the environment's domain mapping."
  }
}
