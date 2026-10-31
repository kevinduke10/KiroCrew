mock_provider "aws" {}
variables {
  account_id     = "123456789012"
  environment    = "prod"
  region         = "us-east-1"
  repository     = "example/repository"
  hosted_zone_id = "ZEXAMPLE"
  domain_name    = "prod.example.com"
}
run "account_bootstrap" {
  command = apply
  assert {
    condition     = jsondecode(aws_iam_role.ci["apply"].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example/repository:environment:prod-apply"
    error_message = "Apply role must trust only this repository's exact environment."
  }
  assert {
    condition     = jsondecode(aws_iam_role.ci["plan"].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example/repository:environment:prod-plan"
    error_message = "Plan and apply require distinct OIDC subjects."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.ci["plan"].policy, "cloudformation:UpdateStack") && !strcontains(aws_iam_role_policy.ci["plan"].policy, "iam:PassRole")
    error_message = "Planning cannot deploy or pass the CloudFormation execution role."
  }
  assert {
    condition     = aws_s3_bucket.storage["state"].bucket == "kc-prod-123456789012-state" && aws_s3_bucket_versioning.storage["state"].versioning_configuration[0].status == "Enabled"
    error_message = "State must be account-specific and versioned."
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.cloudformation.policy, "iam:PermissionsBoundary") && !strcontains(aws_iam_policy.runtime_boundary.policy, "iam:")
    error_message = "CloudFormation-created runtime roles cannot grant IAM authority."
  }
}
