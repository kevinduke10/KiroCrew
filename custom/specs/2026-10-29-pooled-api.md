# SPEC-0007: Cognito and persistent pooled SaaS API

## Intent

Implement the next slice of [SPEC-0004](2026-10-29-company-workspaces.md): verified
Cognito identity, authoritative DynamoDB memberships/resources and durable audit,
connected to a separate serverless HTTP API. Extend
[SPEC-0006](2026-10-29-tenant-authorization.md) without giving the personal gateway
multi-company credentials or treating Shared Crew attribution as authorization.
All implementation and deployment sources belong in custom/. No live deployment
or AWS credentials are required for development.

## Acceptance criteria

- [x] SPEC-0007-AC1: Accept only signed RS256 Cognito access tokens for the configured pool/client, with valid lifetime, required scope and live GetUser confirmation. Unknown keys, ID tokens, wrong issuers/clients and rejected users cannot reach storage.
- [x] SPEC-0007-AC2: Strongly consistent DynamoDB lookups resolve current memberships and exact company/space/resource scope. Malformed records fail closed; composite keys cannot collide through caller-controlled delimiters.
- [x] SPEC-0007-AC3: Reads/edits pass through authorization. Resource versions prevent reading or overwriting a changed ACL/content snapshot; edit never accepts replacement grants/owner/company from the request.
- [x] SPEC-0007-AC4: Private administrator reads persist a conditional append-only access record before content retrieval, with completion/failure events. Audit failure prevents disclosure, and the application role cannot update/delete audit records.
- [x] SPEC-0007-AC5: A Lambda HTTP API composes the adapters, rejects caller-supplied identities, returns bounded JSON responses and redacted failures, and provides managed-login configuration. No credential or customer payload enters logs/error text.
- [x] SPEC-0007-AC6: A reviewable infrastructure template configures Cognito code-flow login, pooled on-demand encrypted tables, least-privilege API permissions and bounded networking/execution. Offline tests exercise signed JWTs, SDK requests and the complete guarded API. Deployment validation remains explicitly unclaimed.

## Implementation

Owning custom contract: this spec covers `saas/auth.py`, `records.py`, `dynamo.py`,
`service.py`, `api.py` and `bootstrap.py`. Upstream
[security](../../docs/architecture/security-deep-dive.md),
[authentication](../../docs/system-specs/modules/dashboard-token-auth.md) and
[public-fork boundaries](../../docs/system-specs/oss-fork-boundaries.md) remain
unchanged. Cognito configuration is customer-provisioned, with no internal pool IDs
or revival of inert SSO routes.

The public client uses Cognito managed login with authorization code + PKCE,
`openid` and `aws.cognito.signin.user.admin`. Tokens are accepted only in the
Authorization bearer header. JWT verification uses PyJWT's RSA implementation and
keys from the configured issuer's JWKS endpoint, never token-supplied URLs. Live
GetUser on every request confirms the same signed subject and current Cognito
acceptance; dependency failure denies access. This requires the admin scope even
though it confers no company-administrator role. App roles/groups come only from
DynamoDB. Pool/client configuration is trusted deployment input.

One records table uses company partition keys and organization-scoped resource
sort keys. Organization records and explicit organization memberships use separate
`ORG#` partition keys. [SPEC-0009](2026-10-29-organizations.md) owns organization
creation, membership management and filtered agent discovery. The content Lambda's
PutItem permission is confined to those organization partitions; it cannot replace
company memberships or resource ACL records. Company-scoped membership records
continue to supply company-administrator authority. Records carry a strict schema version, scope, owner/grants and immutable
revision token. Content is a bounded JSON string (64 KiB) in the resource item;
large S3 documents, vector memory and execution histories are later integrations.
Membership reads are strongly consistent. Reads re-fetch and compare the full
metadata/version; edits condition on the original revision. This guards resource
changes, not an atomic membership-revocation transaction. Every API request checks
membership again; ongoing streams, queued jobs and cancellation remain unbuilt.

A separate audit table accepts conditional PutItem only from the API role; no
UpdateItem/DeleteItem/TTL or content-body fields are supplied. Conditional PutItem
is an application-enforced append contract: IAM PutItem permission itself can
replace an existing item if trusted code omits the condition. This is not WORM
storage or protection against a compromised API process/account administrator. Access confirmation
precedes a private admin read; completion/failure records share the operation ID.
A missing completion receipt also refuses disclosure. This protects against
ordinary application users, not an AWS account administrator with IAM authority.
Deployment must verify policies, backup retention and audit behavior before use.

API routes: public GET `/auth/config`; authenticated GET/PUT
`/companies/{company}/organizations/{organization}/spaces/{space}/resources/{resource}`. PUT accepts only
`content`, edits existing resources and requires an explicit matching If-Match
revision. Returned resource data includes a version suitable for the next edit.
Other content methods/routes are rejected. SPEC-0009 adds authenticated PUT
organization and organization-member routes plus GET agent discovery. Organization
membership mutations require current company administrator authority. No open
self-registration, arbitrary tools or agent execution endpoints are exposed by the
content API. Company onboarding and invitations belong to the separate control API. Trusted provisioning code can serialize validated
membership/resource records; provisioning authority is separate from the API role.

`custom/deploy/pooled-api.yaml` is a SAM source template, not a deployed stack.
It creates an invitation-only user pool, public code-flow client, domain, HTTP API,
Lambda, records/audit tables and narrowly scoped role. Callback/logout HTTPS URLs
and a unique domain prefix are required deployment parameters. Browser PKCE UI and controlled company onboarding are implemented by
[SPEC-0008](2026-10-29-platform-administration.md). SAML/OIDC external providers
remain separate delivery work.
Package only `kirocrew_custom` plus its saas extra for this Lambda; the large core
personal application is not part of its runtime. Deployment bundling instructions
must make this separation explicit. No resources are created in this work session.

Primary implementation references:
[Cognito verification](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html),
[GetUser scope](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUser.html),
[PyJWT](https://pyjwt.readthedocs.io/en/latest/usage.html),
[DynamoDB conditional writes](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_PutItem.html).

## Validation

AC1: `custom/tests/test_saas_auth.py` verifies real RSA signatures with an in-memory
JWKS and frozen PyJWT clock, rejects invalid claims/keys/algorithms and exercises
live-confirmation refusal with an injected Cognito client. One test runs a signed
token through the full API, real authorizer and DynamoDB adapters with fake transport.

AC2–AC4: `custom/tests/test_saas_persistence.py` covers consistent reads, strict
record parsing, company/space scopes, revocation, metadata projections, conditional
revision updates, audit ordering/completion/failure and duplicate audit refusal.
Botocore Stubber validates real SDK request shapes without network access.

AC5: `custom/tests/test_saas_api.py` covers authenticated read/edit, identity
injection, content limits, error redaction, public login configuration and rejecting
unrelated personal-gateway routes. `test_saas_bootstrap.py` verifies deployment
composition, HTTPS configuration and bounded client retries/timeouts without AWS.

AC6: `custom/tests/test_saas_template.py` pins the audit and metadata IAM boundaries.
`AWS_EC2_METADATA_DISABLED=true AWS_DEFAULT_REGION=us-east-1 cfn-lint -t
custom/deploy/pooled-api.yaml -r us-east-1` passed with cfn-lint 1.59.0, including
SAM schema checks. Separately, aws-sam-translator 1.115.0 translated the template
to 12 CloudFormation resources using a placeholder S3 CodeUri substituted only in
memory (no upload). Linting that translated artifact reports only W2001 for the
WebOrigin parameter consumed during translation; the source template passes cleanly.
The custom wheel built offline with cached build tools;
an extracted-wheel Python 3.13 smoke check imported the SaaS handler without the
core package and served public login configuration without creating AWS clients.
Linux Python 3.12 SAM/container packaging has not been run.

Observed command: `/private/tmp/kirocrew-bedrock-venv/bin/python -m pytest -c
setup.cfg -n0 custom/tests` passed 161 tests (includes earlier custom tests).
Targeted flake8, Black/isort checks and Linux-platform mypy passed for the SaaS
modules. The full repository documentation check, custom Markdown links, tracker
merge-base validation and whitespace checks passed. No live Cognito, DynamoDB,
IAM policy simulation or deployment evidence
exists. These changes are local and uncommitted; no resources were provisioned.

## Next steps

Implement the browser PKCE flow and controlled company onboarding/invitations,
then connect shared agents and scoped memory. Before deploying, build in the Linux
Python 3.12 SAM container and review deployment parameters/policies. Live integration
must verify Cognito login/revocation, DynamoDB conditions, privilege auditing,
retention/restore and cross-company tests. No AWS credentials are needed for the
next offline development slice.
