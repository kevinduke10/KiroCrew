# SPEC-0006: Company and space authorization foundation

## Intent

Start the pooled SaaS selected in [SPEC-0004](2026-10-29-company-workspaces.md)
with a small, independently testable access layer in custom/. Preserve company
identity independently of future AWS account placement. This slice is an offline
library, not a login service, deployment or gateway security retrofit.

Owning contracts: this plan defines the custom authorization library;
[Shared Crew](../../docs/system-specs/modules/shared-crew.md) remains an upstream
attribution feature and is not used as a security boundary.

## Acceptance criteria

- [x] SPEC-0006-AC1: Every operation resolves authoritative company membership and resource scope; missing membership and mismatched company/space/resource records deny access.
- [x] SPEC-0006-AC2: Private content admits its owner; ordinary coworkers are denied. Same-company administrators may read only after an audit sink confirms persistence; sink failure prevents content access.
- [x] SPEC-0006-AC3: Shared resources require explicit action grants for a user or one of their current company groups. Running an agent does not grant conversation access or editing.
- [x] SPEC-0006-AC4: Each operation rechecks authoritative membership and grants; no positive authorization cache survives membership revocation.
- [x] SPEC-0006-AC5: Offline tests cover company boundaries, private owner/admin/coworker access, audit ordering/failure, grants, resource substitution and revocation. Limitations and the next integration step are recorded.

## Implementation

`custom/src/kirocrew_custom/saas/access.py` defines immutable identity, resource,
membership and grant records, and a stateless guarded operation. Storage and audit
are injected protocols. Only trusted server code supplies identity, repository,
audit sink and operation callback; these are not request-body fields or agent tools.
Identity must come from a verified issuer/subject adapter. The Cognito adapter
and separate pooled API are implemented in [SPEC-0007](2026-10-29-pooled-api.md).
The repository must read memberships and company-scoped resources authoritatively,
not derive them from token role claims or browser-provided grants.

[SPEC-0009](2026-10-29-organizations.md) adds mandatory organization scope to
resource keys and independent organization memberships. Each operation checks both
current company membership and exact-organization membership. Groups come from the
organization membership, never the company record or an ancestor organization.
Company administrator status alone does not reveal agents in another organization.
Missing organization scope denies access; legacy resources need explicit trusted
migration. The existing platform-operator integration remains separate from ordinary
company roles; it does not confer ordinary agent-directory membership.

READ, RUN, EDIT and MANAGE grants are independent. RUN applies only to agent
definitions. Private owner access permits READ/EDIT; other private mutations and
private agent execution are deliberately undefined and denied in this slice.
Admin exceptions permit READ only, even if a private resource contains stray grants.

Audit `record_access` must return a nonempty receipt only after durable append.
Its event includes a unique operation ID, verified identity, exact scoped resource,
action, injected UTC timestamp and outcome `authorized_before_access`. This records
permission to begin a read, not proof of successful delivery; a later adapter needs
completion/failure events and append-only permissions. Audit failure propagates
before the operation callback. SPEC-0007 supplies a conditional DynamoDB sink;
no in-memory production sink is supplied.

The callback receives the authoritative resource record and must use its complete
company/space/resource key for storage. HTTP handlers and worker adapters must use
this boundary for every operation. Direct database access bypasses the library;
production isolation therefore also requires scoped IAM, data adapters and worker
credentials. No callbacks are exposed to customers. Revocation is checked at each
operation, not atomically with concurrent store changes. Streams and queued work
must recheck on execution/delivery; in-flight cancellation is future work.

There is no AWS placement implementation yet. Company IDs contain no account IDs;
a future authoritative placement directory can route them to pooled or dedicated
resources. The separate SPEC-0007 API applies this library to resource reads/edits. Existing
personal-gateway memory, tool, listing and WebSocket paths do not use it.

[SPEC-0010](2026-10-29-company-roles.md) defines explicit company/organization roles
and normalized legacy record handling. Company Owner/Admin share the existing
private-read audit exception; Organization Admin alone does not receive it.

## Validation

AC1–AC5: `custom/tests/test_saas_access.py` passed all 24 parameterized cases.
Command: `/private/tmp/kirocrew-bedrock-venv/bin/python -m pytest -c setup.cfg -n0
custom/tests` from the repository root passed all 76 custom tests, including the
24 new cases (overlapping counts). Targeted flake8, Black checks, isort and
`mypy --platform linux custom/src/kirocrew_custom/saas` passed. Tracker validation
with `--base HEAD`, `git diff --check`, custom Markdown links and the full
repository `bash scripts/docs-lint.sh` check passed. Tests use injected repositories,
audit sinks and clocks; they do not prove AWS persistence or deployment isolation.
No AWS credentials, deployment, Cognito token verification, DynamoDB adapter or
live durable audit evidence exists. Changes remain local and uncommitted.

## Next steps

The browser PKCE and controlled company onboarding integration is in
[SPEC-0008](2026-10-29-platform-administration.md). Continue scoped agent/memory
and worker integration. Verify the deployed boundary before accepting
customer content. Existing upstream gateway paths are not a multi-company API.
