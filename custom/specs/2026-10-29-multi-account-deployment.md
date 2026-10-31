# SPEC-0012: Consistent Dev, Test and Prod AWS deployments

## Intent

The user authorized Terraform and a GitHub Actions pipeline for three separate
Dev/Test/Prod AWS accounts and explicitly deferred Argo. Account IDs and domains
are not available; the user requested placeholders. Placeholder configuration must
fail before credentials or deployment. Environment accounts remain separate from
application companies/organization trees.

Owning contracts: [company workspaces](2026-10-29-company-workspaces.md),
[pooled API](2026-10-29-pooled-api.md), and
[RAG knowledge](2026-10-29-rag-knowledge.md).

## Acceptance criteria

- [x] SPEC-0012-AC1: Record the three-account topology, shared configuration strategy, infrastructure ownership and promotion design, distinguishing recommendations from implemented deployment automation.
- [x] SPEC-0012-AC2: Shared Terraform definitions deploy equivalent service topology into distinct Dev/Test/Prod accounts, with explicit environment inputs, expected-account checks, pinned tool/provider versions and independent encrypted, versioned, locked remote state.
- [x] SPEC-0012-AC3: Account bootstrap provisions bounded CI roles and state/artifact storage; GitHub OIDC trust restricts repository and environment, with short-lived credentials and no stored AWS access keys. Test/Prod cannot be reached using Dev deployment credentials.
- [x] SPEC-0012-AC4: The pipeline builds one immutable release, records its source revision and digest, and promotes those same application bytes through Dev and Test to approved Prod deployment. Each environment receives its own reviewed plan; no plan/state is reused across accounts.
- [ ] SPEC-0012-AC5: Every account has separate authentication, tables, documents, RAG index, secrets and audit records. Tests prove wrong-account deployment refusal and data isolation; no automatic production-data replication to lower environments exists.
- [ ] SPEC-0012-AC6: Drift detection, deployment serialization, smoke/isolation checks, release rollback and data-migration recovery procedures are implemented and verified. Live account deployment evidence is recorded before readiness is claimed.

## Implementation

`custom/deploy/terraform/` contains account bootstrap, a shared application module
and Dev/Test/Prod roots. Terraform 1.14.0 and AWS provider 6.20.0 are pinned, with
signed provider checksums for Linux/macOS. Application roots use independent S3
state and native locking with expected-account guards. Bootstrap is prepared into
separate generated account directories; operator-owned bootstrap state is migrated
to a separate key inaccessible to CI. No AWS account or DNS delegation is created.

Bootstrap creates private/versioned/encrypted state and artifact buckets, exact
GitHub repository/environment OIDC trust for distinct plan/apply roles, a scoped
CloudFormation execution role and runtime permissions boundary. Runtime roles
cannot acquire IAM/deployment/state access. Artifact writes under `releases/` are
create-only and deletion is denied by bucket policy. Plan roles cannot apply or
pass the CloudFormation role. Explicit operator changes to these policies remain
possible; this is not an immutable compliance archive.

The application module owns the CloudFormation stack plus ACM, API custom domain
mapping and Route53 records in a supplied, delegated public hosted zone. Nested
application resources belong only to CloudFormation. The SAM template optionally
creates the Bedrock KB/data source and retained S3 vector bucket/index in one
dependency graph; Terraform enables this managed mode with an explicit embedding
model/dimension configuration. Existing external KB parameters remain supported by
standalone SAM. No embedding model is defaulted. S3 Vectors/model availability and
entitlement must be verified in each selected Region.

`custom/scripts/deployment/` supplies fail-closed account/configuration validation,
Linux release ZIP manifests, version-pinned S3 Lambda artifacts, private saved-plan
bundles, exact-plan apply, protected environment preflight and bounded drift
checks. The same bytes are copied into each account. Each plan receipt binds its
account, environment, configuration and tooling commit; plans expire after 24 hours.
Plans/templates/variables remain in private S3 rather than public GitHub artifacts.
Terraform plans expose the stack URL/parameters; reviewers must inspect the bundled
SAM template for nested-resource effects. Terraform deletion/replacement refusal
is not a nested CloudFormation change-set analysis.

The deployment workflow runs only on manual dispatch from `main`. It checks all
six `*-plan`/`*-apply` environments for main-only branch rules and requires Prod
reviewers with self-review disabled. Administrator bypass must be disabled in the
GitHub UI; the documented REST environment response does not expose it. No missing
protection is silently substituted with an unprotected deployment.

Release tests/build → Dev plan/apply → Test plan/apply → Prod plan/approved apply.
The default post-apply probes check the custom domain, public web shell and Cognito/
knowledge configuration; they do not exercise authenticated tenant/RAG behavior.
Both offline authorization coverage and live validation gaps are explicit. Failed
stages stop promotion. Termination protection is enabled after creation; stack
and retained-data removal requires an explicit operator process.

Rollback takes a prior successful main-branch deployment run ID and reuses its
exact release artifact through the same promotion path. It does not roll back
Terraform configuration or data. The GitHub release artifact must still exist
(90-day retention). Schema/index changes need backward-compatible migration or
restore/cutover; no automatic data rewind or destructive recovery exists.

The opt-in weekday drift workflow shares deployment concurrency and uses the
last successful deployed variables plus supported CloudFormation drift detection.
It reports failures in Actions without auto-repair or messaging integrations.
The PR workflow performs credential-free validation, mock-provider plans and tests.

Complete operator commands and protection requirements live in
[the custom README](../README.md#dev-test-and-prod-accounts). Thin `.github/workflows/`
entry points are documented integration bridges; feature code stays in `custom/`.

Primary references:

- [Terraform S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3)
- [GitHub AWS OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [GitHub environment REST contract](https://docs.github.com/en/rest/deployments/environments)
- [S3 conditional-write enforcement](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html)
- [CloudFormation S3 vector indexes](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-s3vectors-index.html)

## Validation

AC1–AC4: Account configuration/refusal, immutable upload collision checks, release
hashes, exact saved-plan execution, account/config/revision/expiry binding,
production-reviewer/branch checks, rollback provenance and workflow ordering are
covered by `custom/tests/test_deployment.py`. Terraform mock-provider tests cover
OIDC separation, plan/apply authority, state configuration, required runtime
boundaries and application/DNS dependency planning.

Local results: all four Terraform roots validate; bootstrap and application mock
tests pass (two runs); cfn-lint and actionlint pass. All 294 custom Python tests passed (16 deployment tests), with targeted Black,
isort and flake8 checks clean. Documentation links and the tracker validate.
The committed null account/model/zone values intentionally fail CLI configuration
before cloud access. No AWS credentials, real plan/apply, GitHub environment
configuration, hosted-zone delegation, remote workflow execution, commit or push
has occurred.

AC5–AC6 remain open for live IAM, login, authenticated company/subtree isolation,
RAG ingestion/revocation, restore/rollback and service-specific drift checks in the
actual accounts. Offline tests do not establish these cloud guarantees.

## Next steps

When accounts/domains exist, replace placeholders, bootstrap each account with its
own operator identity, migrate bootstrap state, configure GitHub environments and
perform the first gated deployment. Verify live isolation/ingestion and recovery
before claiming production readiness. SPEC-0011's future worker integration remains
independent and open. Argo is not included.
