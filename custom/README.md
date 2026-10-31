# Repository-local custom layer

Our features live here, in the same Git repository as Kiro Crew. Upstream merges
normally leave these paths alone because upstream does not own them. Keep this
directory tracked: `.gitignore`, Git submodules, generated overlays and a separate
repository are not part of this layout. A hard reset or clean can still discard
local work; a future upstream path collision must be resolved deliberately.

## Layout

| Path | Ownership |
|---|---|
| `src/kirocrew_custom/bedrock/` | Native SDK inference, ACP transport and MCP tool loop. |
| `src/kirocrew_custom/saas/` | Company/space authorization, Cognito, persistent adapters and a separate Lambda API. |
| `src/kirocrew_custom/integration/` | The custom agent-spec mirror behind a lazy core bridge. |
| [specs/](specs/README.md) | Feature plans, acceptance criteria and append-only checkpoints. |
| [docs/](docs/README.md) | Custom development and upstream-sync procedures. |
| `scripts/` | Read-only tracker and history inspection tools. |
| `tests/` | Offline runtime, tracker and integration checks. |
| [deploy/lean-pilot.json](deploy/lean-pilot.json) | Deferred dedicated-account option; not deployable. |
| [integration.json](integration.json) | Core patch ownership and upstream compatibility reference. |

## Install and run

From the repository root, use the same Python environment for both packages:

```bash
python3 -m pip install -e . -e './custom[bedrock]'
kirocrew config set agent.acp_backend bedrock
kirocrew config set agent.member_acp_backend bedrock
kirocrew config set agent.model '<your Bedrock model ID or inference profile ARN>'
```

Fresh installs default both backend fields to Bedrock. Existing explicit KAS/CLI
choices need both settings changed. Keep model `auto` until a model is available;
creating mates needs no AWS setup, and chat returns `Cannot reach AI` with setup
guidance. Backend or inference failure never chooses another harness.

Use the launcher from the Python environment where these editable packages are
installed. A `kirocrew` command pointing inside `/Applications/KiroCrew.app` runs
that installed bundle, not this checkout; source edits do not update the desktop
application. Install/rebuild the fork before changing that application's config.

Restart the gateway after changing backend. `kirocrew-custom` is a local package;
it is not published to PyPI. Build a separate wheel from `custom/` when making a
packaged distribution, and install it beside the core wheel. Neither package copies
its source into the other's namespace. The core's Bedrock adapter launches
`python -P -m kirocrew_custom.bedrock` with its usual sandbox and permission gate.

Optional dependencies belong to this package's `bedrock` extra. The core's install
probe reports the custom package and SDK packages when missing. Other backends
continue to import without the custom package installed. The custom package targets
the core version range declared in [pyproject.toml](pyproject.toml); changing that
range requires compatibility verification, not only a version edit.

Bedrock accepts text and projected MCP tools through Converse. Non-Converse models,
token streaming and native session reload remain outside the current implementation;
SPEC-0001 records the open coverage requirement. Development and mocked tests need
no AWS credentials. Live inference still requires AWS credentials and Region under
the existing sandbox policy.

## Track, verify and inspect history

```bash
python3 custom/scripts/spec_tracker.py list
python3 custom/scripts/spec_tracker.py history SPEC-0001
python3 custom/scripts/spec_tracker.py show SPEC-0001 --at HEAD
python3 custom/scripts/spec_tracker.py check --base HEAD
python3 -m pytest -c setup.cfg -n0 custom/tests
```

The history tool reads both the repo-local paths and the original committed plan
locations. A plan relocation appends an explicit migration event; old events are
preserved. See [the workflow](docs/spec-driven-development.md) and
[upstream sync](docs/upstream-sync.md). The dedicated
[Custom Layer workflow](../.github/workflows/spec-tracker.yml) explicitly installs
and tests this nested package; the upstream default testpaths do not collect it.

The selected SaaS starting architecture uses pooled managed/serverless services with
logical company and space isolation. See [SPEC-0004](specs/2026-10-29-company-workspaces.md)
and the [cost scenario](deploy/pooled-serverless-estimate.json). Dedicated accounts
remain a future option. The authorization foundation in
[SPEC-0006](specs/2026-10-29-tenant-authorization.md) does not make the existing
personal gateway safe for multiple customer companies.

## Pooled API development

Install both custom extras for local backend work:

```bash
python3 -m pip install -e './custom[bedrock,saas]'
python3 -m pytest -c setup.cfg -n0 custom/tests
```

[SPEC-0007](specs/2026-10-29-pooled-api.md) owns the Cognito/DynamoDB API.
The Lambda entry point is `kirocrew_custom.saas.bootstrap.handler`. It exposes
managed-login configuration at `GET /auth/config` and guarded resource GET/PUT at
`/companies/{company}/organizations/{organization}/spaces/{space}/resources/{resource}`. Send the Cognito **access**
token in `Authorization: Bearer ...`. PUT accepts only `{"content": ...}` with
`Content-Type: application/json` and the quoted revision from the GET response's
ETag in `If-Match`. Editing grants, identity or membership through this API is denied.
Resource content is limited to 64 KiB. Uploaded knowledge has a separate optional
S3/Bedrock API described below; agent jobs are not connected yet. The API never forwards requests to the personal gateway.

The [SAM template](deploy/pooled-api.yaml) supplies an invitation-only Cognito pool,
public code-flow client, API Gateway authorizer, Lambda, on-demand records table
and a separate audit table. Use a browser PKCE client with a random per-login state
and verifier, S256 challenge, and the exact callback URL registered in Cognito.
The configuration endpoint is not itself a browser login UI or external-company
SSO integration. No client secret belongs in a browser. The self-service Cognito
scope needed by GetUser does not grant application administrator access.

Review and build the template before deploying (commands below create no AWS stack):

```bash
cfn-lint -t custom/deploy/pooled-api.yaml -r us-east-1
sam build --use-container --template-file custom/deploy/pooled-api.yaml
```

The custom Makefile installs this distribution without its core dependency and
then installs only the `saas` extra's dependencies. The Lambda imports only the
standalone `kirocrew_custom.saas` tree. Use the Linux Python 3.12 SAM build container
for deployment artifacts, especially cryptography's native dependencies; a macOS
wheel smoke check does not prove Lambda compatibility. The template's initial
target is commercial AWS US East (N. Virginia). Set exact HTTPS callback/logout URLs,
browser origin and a unique Cognito domain prefix at deployment. No deploy command
has been executed, and offline validation is not proof of deployed IAM behavior.

Trusted provisioning must create company membership and resource records before
this API can serve them. `saas.records.membership_item` and `resource_item` validate
and serialize records; `saas.dynamo.encode_item` produces low-level SDK attributes.
Use the verified Cognito issuer/subject, never an email or browser role claim.
Grant that provisioning process its own authority; the content API role cannot
create company membership records or update resource ACLs. Organization membership
writes are separately authorized and limited to organization partitions. Every resource/ACL/content change must assign a
fresh revision. Company onboarding, invitations and company role changes run through the separate
control Lambda described below.

Production verification must cover pool login/revocation, actual conditional writes,
API policy conditions, audit retention/restore, cross-company access and deployment
packaging. Shared API credentials can read the pooled records table; agents and
untrusted tool workers must never receive those credentials. Tenant isolation here
is enforced by the trusted API and scoped data operations, rather than separate
AWS accounts. Workload-specific tool permissions are still to be implemented.

## Organizations and agent visibility

[SPEC-0009](specs/2026-10-29-organizations.md) implements the organization API and web panel.
Organizations may contain nested sub-organizations. Company administrators create
and manage them through authenticated routes under
`/companies/{company}/organizations/{organization}`:

- `PUT` with `{"name": "HR", "parent_id": null}` creates a root organization.
  Supply an existing same-company organization ID for `parent_id` to create a child.
  Parent links are immutable; duplicates return a conflict. Creation does not grant
  the creator ordinary agent visibility.
- `PUT /members` with `{"subject": "verified-cognito-subject", "active": true,
  "groups": []}` assigns an existing company member to this exact organization.
  Set `active` to false to revoke access. The issuer comes from the authenticated
  administrator. Organization membership cannot create a company administrator.
- `GET /agents` lists only readable agent IDs and their space IDs in that exact
  organization. Parent, child and sibling memberships do not propagate. Private
  agents remain hidden from ordinary discovery unless owned by the caller.
- `GET/PUT /spaces/{space}/resources/{resource}` uses the same membership boundary.
  Existing explicit read/edit/run grants remain necessary.

The directory uses metadata-only queries, bounded to ten pages of fifty records;
it returns an error instead of an incomplete directory if the bound is exceeded.
It returns no hidden-resource counts or storage cursors. No worker execution or
streaming API is introduced. The web administration shell includes organization
creation, membership assignment/revocation and agent discovery. Select a company,
then enter the organization ID (initially `root`); company administrators also see
creation and membership forms. Deployed AWS verification remains outstanding. The previous company-only resource URL is rejected;
trusted provisioning must assign organization scope, write a fresh resource revision
and explicitly provision organization memberships before serving older records.

## Browser and Superman administration

The custom Lambda serves a browser administration shell at `/`. It signs in through
Cognito code flow with PKCE, lists authorized companies, and supports company
creation, renaming, suspension/reactivation and invitations. Superman sees every
company; company administrators can invite collaborators only into their own active
company. Customer text is rendered as text, and access tokens stay in memory.
Reloading requires sign-in again. The browser shell does not provide the future
agent/memory workspace interface.

The separate control Lambda handles `/me`, `POST /platform/companies`,
`PUT /companies/{company}` and `POST /companies/{company}/invitations`. Creation and
invitations require an `Idempotency-Key`; reuse the same key and body after a network
failure. Company updates require both the quoted revision in `If-Match` and an
`Idempotency-Key`; receipts make a successful update retry-safe. Onboarding
creates a root organization and the creator's company/root memberships atomically.
Invitations create only company and root-organization membership, never inherited
access to child organizations. An existing membership cannot be overwritten by
sending another invitation. Cognito sends a sign-in email only when the deployed
invitation operation creates a new account. No emails have been sent during tests.

**Superman is a platform role, not a company administrator flag.** Its grant is a
strongly consistent record in the separate PlatformRoles table, keyed by verified
Cognito issuer/subject. The API roles can read this table but cannot write it. A
trusted AWS operator must bootstrap and audit the initial grant after deployment;
there is no first-user promotion or browser endpoint that grants Superman. Use the
`platform_role_item(identity, active=True)` serializer in `saas.platform`, with the
actual verified identity, and `encode_item` for the SDK write. Bootstrap/revocation
must use the operator's separate IAM authority and audit procedure. Do not put AWS
keys into the browser. No actual grant has been created in this checkout.

Setting that grant to `active=False` removes the global override on the next
operation. Existing separately granted company memberships remain valid and must
be revoked separately if access should end entirely. Every Superman content read
or edit is audited, even when that user also owns the resource. Platform operations
record intent before execution and outcome afterward; failure to persist audit
prevents the action or response. The role does not bypass tool approval or the OS
sandbox. Ordinary agent discovery retains exact-organization visibility.

The SAM template keeps platform grants read-only to both Lambda roles and separates
company/Cognito mutation permissions from the content API. The content API's existing
organization-management permission remains restricted to organization records.
Suspension blocks ordinary resource and directory access and new invitations.
Platform support may inspect suspended-company content through the audited path.

For the generated API hostname, first create the stack using placeholder HTTPS
callback/logout/origin parameters, then update those parameters to the returned
ApiUrl (callback/logout end in `/`). The browser and API then share one HTTPS origin.
A custom hostname/reverse proxy can instead provide the configured origin. No cloud
resources, DNS changes, invitations or role grants are performed by these source
changes. Linux SAM packaging and live isolation tests are still required.

Run the browser's deterministic PKCE checks with Node.js 22 or newer:

```bash
node --test custom/tests/browser/*.test.mjs
```

[SPEC-0008](specs/2026-10-29-platform-administration.md) records the implementation,
audit contract and remaining validation work.

## Company and organization roles

The company roles are **Company Owner**, **Company Admin** and **Member**.
**Organization Admin** is assigned separately for each organization. Superman
remains an independently provisioned platform role and cannot be assigned through
the company membership API.

New companies give the creator Company Owner membership and a matching owner
identity on the company record. The Owner can appoint/demote Company Admins and
transfer ownership to an existing active company member. Transfer updates both
memberships and the company owner/revision atomically; the former Owner becomes a
Company Admin. The Owner cannot be deactivated or demoted without a replacement.
Company Admins can invite Members and deactivate/reactivate ordinary Members.
Only Owner/Superman can invite new Company Admins. Owner can rename the company;
platform suspension/reactivation remains Superman-only.

Use `PUT /companies/{company}/members/{subject}` with the current company revision
in quoted `If-Match`, and `{"role":"company_admin","active":true}` (or
`company_owner`/`member`). This edits existing memberships; ownership requires an
already-active target. A role update changes the company revision, so reload after
success or ambiguous delivery. The browser includes the role/ownership form and
copies a newly invited user's subject into it. Role audits contain the target,
previous/requested role and active status. Failed intent audit prevents mutation;
failed completion audit refuses the response after the attempted operation.

Organization Admin can manage ordinary membership/groups only in that exact
organization. It cannot create organizations, appoint/demote peer Organization
Admins, edit company administrators' organization assignments or change company
roles. Owner/Company Admin/Superman appoint Organization Admins through
`PUT /companies/{company}/organizations/{organization}/members` with
`{"subject":"...","active":true,"groups":[],"role":"organization_admin"}`.
Only existing company members can receive organization roles. Use the browser's
Load organization permissions button to enable scoped administration from fresh
server permissions; changing organizations clears the prior controls.

Role grants do not automatically reveal private content or agents in parent,
child or sibling organizations. Company Owner/Admin private-content access keeps
the existing audited path and exact organization membership requirement. Ordinary
agent access still needs explicit resource grants. Billing and additional agent
management surfaces remain future integrations, not new APIs in this change.

Legacy records without a role map `administrator=true` company memberships to
Company Admin; ordinary and organization memberships map to Member. No record is
automatically promoted to Owner. Superman can explicitly assign the initial Owner
for a legacy company with no owner pointer. A role/administrator mismatch, unknown
role or a company role in an organization record is rejected. The stored boolean
remains a validated compatibility field, not a second source of authority.

[SPEC-0010](specs/2026-10-29-company-roles.md) owns this role contract and validation.


## Company and organization RAG knowledge

[SPEC-0011](specs/2026-10-29-rag-knowledge.md) provides the authenticated knowledge
API and web controls. Company-wide documents are available in every organization
in that company. Organization documents are local by default; select direct child
organizations to share with each selected child and its entire subtree. Sharing
never grants access to sibling organizations, parent organizations, other companies
or other organizations' agents. Only the owning organization's administrators or
company administrators can publish/change its documents; company documents require
company administration. Retrieval requires current company and exact-organization
membership, including for company administrators. Platform support privileges alone
do not grant knowledge retrieval or publishing.

The web UI adds upload, status, sharing and search preview when knowledge is
configured. Initial uploads support UTF-8 `.txt`, `.md`, and `.pdf`, up to 1 MiB.
PDF indexing success depends on Bedrock parsing; check the explicit index status.
Documents have immutable IDs/content: retry the same upload after a failure, or use
a new ID for a replacement. Disable the previous document to withdraw it. Removing
a child share or disabling a document blocks subsequent authorized retrieval even
while its vectors remain in the index; this is access revocation, not physical
storage erasure. Retained S3 versions/vector deletion need an operator retention
process. Already delivered content cannot be recalled.

Authenticated routes (Cognito access token, JSON writes):

- `PUT /companies/{company}/knowledge/documents/{document}` accepts exactly
  `organization_id` (null for company-wide), `title`, `extension`, base64 `data`,
  and `shared_children` (direct-child IDs, empty by default in the UI).
- `GET` on that path returns the manifest and current index status to its managers.
- `PUT` on that path plus `/sharing` accepts `shared_children` and `active`, with
  the loaded manifest's quoted `revision` in `If-Match`.
- `POST /companies/{company}/organizations/{organization}/knowledge/query` accepts
  `{"query":"..."}` and returns authorized cited excerpts for preview.
- `POST /companies/{company}/organizations/{organization}/spaces/{space}/agents/{agent}/knowledge/query`
  accepts the same query and additionally requires RUN on that exact agent.

Retrieval combines permitted sources, filters before vector search, and checks
fresh authoritative manifests and memberships before returning snippets. The pilot
bounds each company to 128 queried manifests, 32 selected children per document,
2,000 query characters and five returned excerpts of at most 4,000 characters.
Exceeding the manifest bound fails rather than silently searching a partial set.
Queries may span multiple Bedrock calls; measure latency against API Gateway's
request deadline before production and move larger workloads to queued retrieval.
Returned text is labeled untrusted context. The SaaS worker still needs to call the
agent endpoint with delegated user identity and preserve source provenance through
context reuse; this does not yet make deployed agent chat use RAG automatically.

Deployment has not been performed. The Terraform Dev/Test/Prod workflow below
creates the KB, data source and S3 vector index from the shared SAM template with
an explicit embedding model/dimension setting. It also enables the dedicated
knowledge Lambda and browser controls. Standalone SAM can use an existing KB/data
source through `KnowledgeBaseId` and `KnowledgeDataSourceId`; configure its service
role to read the source bucket/metadata and invoke the chosen embedding model.
`EmbeddingModelArn` is an explicit required template input. Verify live ingestion,
IAM, cross-company/subtree retrieval, revocation and latency before production.

The adapter uses S3 originals plus `.metadata.json` sidecars with
[direct ingestion](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-direct-ingestion-add.html)
and [filtered Retrieve](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Retrieve.html).
The browser accesses the web API only; storage and Bedrock credentials stay on the
server. Upstream local personal-agent knowledge remains separate.


## Dev, Test and Prod accounts

[SPEC-0012](specs/2026-10-29-multi-account-deployment.md) owns the Terraform and
GitHub Actions deployment implementation. Argo is deferred. Each environment uses
a distinct AWS account with independent state, IAM roles, Cognito login, tables,
RAG documents/indexes, DNS/TLS and runtime configuration. The three application
roots call the same module and deploy the same SAM template/release bytes.

| Location | Purpose |
|---|---|
| [environments.json](deploy/environments.json) | Reviewed account/Region/domain/embedding inputs; null placeholders deliberately prevent deployment. |
| `deploy/terraform/bootstrap/` | Account state/artifact buckets, OIDC plan/apply roles, CloudFormation role and runtime permissions boundary. |
| `deploy/terraform/environments/{dev,test,prod}/` | Separate locked application state and shared stack module. |
| `deploy/terraform/modules/application/` | CloudFormation stack, ACM certificate, API custom domain/mapping and Route53 records. |
| `scripts/deployment/` | Configuration validation, release integrity, saved plans, apply, rollback provenance and drift checks. |
| [custom-deploy.yml](../.github/workflows/custom-deploy.yml) | Manual trusted-main release build and Dev → Test → Prod promotion. |
| [custom-deploy-environment.yml](../.github/workflows/custom-deploy-environment.yml) | Separate plan/apply jobs and environment gates. |
| [custom-drift.yml](../.github/workflows/custom-drift.yml) | Opt-in scheduled Terraform and CloudFormation drift checks; never auto-applies. |
| [custom-infrastructure.yml](../.github/workflows/custom-infrastructure.yml) | Credential-free PR validation and mocked Terraform tests. |

### Configure and bootstrap

Nothing has been deployed. Keep placeholders until the three accounts and domains
exist. Fill every environment in `deploy/environments.json` with a different
12-digit account ID, commercial AWS Region, HTTPS web origin, same-origin callback
and logout URLs, unique Cognito prefix, public Route53 hosted zone ID owned by that
account, and an entitled embedding foundation-model ARN/dimension pair. The managed
RAG stack uses S3 Vectors; confirm that the Region/model supports this configuration.
No model ID is supplied by the application. The standalone SAM template now also
requires `EmbeddingModelArn`; Terraform always enables managed knowledge.

Use public hosted zones with delegation already configured. For example, delegate
`dev.example.com` and `test.example.com` to zones in their respective accounts.
The application module creates records/certificates inside the supplied zones;
it does not register domains or change a parent account's delegation. The pipeline
checks that the three account IDs, web origins and Cognito prefixes are distinct.

Install Terraform 1.14.0 and Python 3.12 with boto3/PyYAML. Bootstrap is an explicit
operator step using an account administrator profile; the pipeline cannot create
its own initial trust or state bucket. The helper only prepares local files:

```bash
python -m custom.scripts.deployment bootstrap --environment dev
AWS_PROFILE=dev-admin terraform -chdir=custom/deploy/.local/bootstrap/dev init
AWS_PROFILE=dev-admin terraform -chdir=custom/deploy/.local/bootstrap/dev plan -out=bootstrap.tfplan
AWS_PROFILE=dev-admin terraform -chdir=custom/deploy/.local/bootstrap/dev apply bootstrap.tfplan
```

Repeat with `test`/`prod` and their own profiles. Each generated directory has its
own state, account guard and variables. If an account already has the GitHub OIDC
provider, set `TF_VAR_existing_oidc_provider_arn` to reuse it before planning.
Never apply the same local bootstrap state against another account. Generated
`.local/` files are ignored by Git. Preserve their state until migration succeeds.

After bootstrap, add this `backend.tf` inside that environment's generated
bootstrap directory, then run `terraform init -migrate-state
-backend-config=backend.hcl` in that directory with the same operator profile:

```hcl
terraform {
  backend "s3" {}
}
```

The generated backend configuration uses the account's state bucket,
`bootstrap/terraform.tfstate`, native locking and an expected-account check.
Application state uses the separate `application/terraform.tfstate` key. CI roles
cannot access bootstrap state or rewrite their own roles. Keep operator state
backups protected; do not commit state or saved plans. Existing deployed stacks
must be imported into the correct application state before adoption.

### Configure GitHub environments

Create `dev-plan`, `dev-apply`, `test-plan`, `test-apply`, `prod-plan` and
`prod-apply`. For all six, select custom deployment branch rules with exactly one
**branch** rule named `main` (no tag rules). Require reviewers and prevent
self-review for `prod-apply`; choose a reviewer other than the person dispatching
the deployment. Disable administrator bypass in the environment UI and protect
`main`/workflow changes. The preflight reads GitHub's API and rejects missing
reviewers, self-review allowance or broad branch rules. Administrator bypass is an
operator setting because the documented environment REST response does not expose
that setting. Ensure the repository's GitHub plan supports these protections.

No AWS access-key secrets are needed: the workflow exchanges GitHub OIDC for the
exact environment's short-lived plan/apply role. The trust policy binds repository,
environment and audience. Plan roles cannot update stacks or write application
state. CloudFormation may create only bounded runtime roles with the required
permissions boundary. Deploy roles remain privileged within their account: review
infrastructure and workflow changes accordingly.

### Deploy, review and recover

Dispatch **Custom Dev Test Prod deployment** from `main`. It validates all config
and GitHub gates before AWS credentials, runs the offline custom/browser tests,
builds a Linux Python 3.12 Lambda ZIP once and publishes its digest manifest. Each
account receives identical application bytes; account-local template locations and
parameters differ. Artifact buckets require create-only writes for `releases/`
and deny deletion there. GitHub keeps the release artifact for 90 days; account
artifact buckets retain released objects until an explicit operator retention
policy change.

Each environment creates its own Terraform saved plan. The workflow summary links
to a private, versioned S3 ZIP containing the binary plan, receipt, variables and
SAM template. Review with an authorized operator identity and the checked-out
workflow revision; use `terraform show` for the binary plan and inspect the SAM
template as well. Terraform's plan shows the CloudFormation stack change, not a
full nested-resource change set. Nested CloudFormation replacements must be
reviewed explicitly; the automated deletion/replacement refusal applies to
Terraform-managed resources. Plans are never uploaded to public GitHub artifacts
or printed as JSON in logs. Their digest binds account, environment, configuration,
source revision and exact plan bytes; they expire after 24 hours.

Prod apply starts only after the protected environment gate. Apply uses that
saved plan and never replans silently. A stale/expired plan requires a new run and
new approval. Deployment jobs and drift share a non-cancelling concurrency group;
S3 state locking provides an additional guard. Termination protection is enabled
after stack creation, Terraform refuses stack destruction and CloudFormation
retains tables, source objects and vector resources. These controls do not replace
backups or review of nested stack updates.

After apply, public probes check the custom web domain, login configuration and
knowledge enablement. They do **not** claim an authenticated tenant/RAG isolation
or ingestion test. Provision synthetic test identities/data and perform those
live checks before production readiness; the tests currently run in CI cover those
behaviors offline. A failed apply/health check stops promotion. Inspect actual
stack/state status before retrying; no automatic destructive recovery is attempted.

For application rollback, dispatch the workflow with `rollback_run_id` set to a
previous **successful** run of this deployment workflow on `main`. It downloads
and verifies that run's exact release bytes instead of rebuilding them, then
promotes through the same gates. Rollback requires the GitHub artifact still to
exist. It does not rewind account config, Terraform modules or data. Database/index
changes require backward-compatible migrations or an operator restore into new
resources followed by verified cutover. Never use `terraform destroy` as rollback.

After all environments have a successful deployment, set repository variable
`CUSTOM_DEPLOYMENT_ENABLED=true` to enable weekday drift checks (or dispatch that
workflow manually). Drift checks use the last successful deployment configuration,
compare Terraform state and request CloudFormation resource drift detection.
Unsupported AWS resource drift still needs service-specific verification. A drift
failure reports in Actions; no extra notifications or auto-repair are configured.

Local validation uses `terraform init -backend=false`, `terraform validate`,
mock-provider `terraform test`, `terraform fmt -check`, cfn-lint, actionlint and
`python -m pytest -c setup.cfg -n0 custom/tests`. These checks never provision AWS.
