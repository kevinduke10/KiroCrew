# SPEC-0008: Browser onboarding and Superman administration

## Intent

The user authorized browser sign-in and controlled company onboarding and requested
a Superman role able to administer every company. This is an explicit platform
operator exception to company boundaries, not permission for companies to share
content. Preserve the organization/sub-organization requirements in
[SPEC-0004](2026-10-29-company-workspaces.md); agent visibility and execution remain
separate unfinished work. Extend [SPEC-0007](2026-10-29-pooled-api.md) in custom/.

## Acceptance criteria

- [x] SPEC-0008-AC1: An active Superman grant is resolved by verified issuer/subject from a separate authoritative table on each request. No company-admin, token group, body flag or public endpoint can grant or change that role.
- [x] SPEC-0008-AC2: Superman can list/create companies, rename/suspend/reactivate them and invite company administrators/members. Company administrators can invite only into their own active company. Ordinary memberships never grant cross-company administration.
- [x] SPEC-0008-AC3: Platform administration and Superman content reads/edits persist audit intent before action and outcome afterward; audit failure prevents disclosure/action. Company lifecycle and onboarding writes are conditional/transactional and retry-safe through idempotency keys.
- [x] SPEC-0008-AC4: A browser supports PKCE sign-in, callback state verification, logout, company selection, company onboarding/status and invitation forms. Access tokens stay in memory; only one-use PKCE state/verifier persist in session storage. UI visibility never substitutes for server authorization.
- [x] SPEC-0008-AC5: Deployment separates control-plane mutation permissions from the content API, keeps platform grants read-only to both Lambda roles, and packages the browser assets. Offline tests verify privilege escalation denials, revocation, company boundaries, audits, idempotency, SDK shapes and PKCE. Live deployment is not claimed.

## Implementation

Owning custom contract: this plan covers the platform role/directory, control-plane
API and browser assets, and the new integration in access/service/bootstrap.
Upstream governance, sandbox and inert SSO endpoints remain unchanged. Superman
is a SaaS application role; it never bypasses OS/tool governance or supplies AWS
credentials to an agent.

A dedicated PlatformRoles table contains schema-validated active `superman` grants
keyed by a digest of the verified identity. Bootstrap/revoke these records through
a separate trusted AWS operator process; neither application Lambda may mutate
that table. No default user, email match, first-login promotion or magic token claim
exists. Role revocation applies on the next operation; in-flight cancellation is
not implemented. All platform access, including owner content access, is audited.
Superman may READ/EDIT content through the guarded API; RUN/delegation is not added.
Company Owner/Admin retain their audited private-read exception and cannot edit private
coworker content. Audit remains conditional DynamoDB append, not WORM storage.

Control metadata lives in a separate ControlTable. Creation atomically writes a
company, initial Company Owner, root-organization membership, directory
pointer and a root organization in the organization service's records table.
A company ID derives from the actor and idempotency key, independently of AWS
account placement. Retries with different parameters conflict. Invitations use
Cognito AdminCreateUser email delivery, or resolve an existing account after a
UsernameExists response; email is not a privilege assertion. Membership assignment
is transactional with its directory pointer and request receipt. Duplicate
invitations cannot overwrite an existing membership or silently change its role.
No invitations are sent during offline development. A Cognito invitation can exist
without a membership after storage failure; retry reconciles the same account.

Suspension rejects subsequent ordinary content access and invitations. Superman
may inspect suspended-company metadata/content through audited paths. Directory
listings are paginated and ordinary pointers are revalidated against current
membership and company state. Every operation carries one explicit company ID;
platform access never merges content into another company's feeds or memory.

The browser is a small independent shell served from the custom Lambda, with no
upstream website edits. All strings live in its en-US catalog. It renders customer
text with textContent. OAuth state/verifier are random per login, one-use and
expire; callbacks scrub code/error values from history before token exchange.
Only public-client code flow/S256 is used. Logout clears local state and navigates
to Cognito logout. Refresh tokens are not retained; a reload requires sign-in.
This is a functional administration shell, not the future agent workspace UI.

The content Lambda gets role/company lookup permissions. Its existing organization
metadata writes remain restricted to organization partitions; platform-aware
organization management is audited and accepts the explicit Superman authority. A second control Lambda
gets scoped control/member writes and Cognito invitation permissions. Both can
append audit events but cannot mutate platform grants or delete audit records.
Deployment parameters must point callback/logout/origin to the browser's HTTPS
host. For the default API hostname, deploy with a placeholder callback then update
those parameters to the returned ApiUrl; this avoids a CloudFormation dependency
cycle. Production DNS/hosting and Linux SAM packaging still require verification.

## Validation

AC1–AC3: `custom/tests/test_platform_administration.py` covers platform grant
validation/revocation, cross-company content and lifecycle access, browser role
injection, ordinary administrator denials, company suspension/reactivation,
transactional root onboarding, retry receipts, invitation reconciliation, private
read/edit audit ordering and fail-closed audit behavior. Native botocore Stubber
validates creation/invitation transaction shapes. A real HTTP organization route
proves Superman support access uses the audited administrative path.

AC4: `node --test custom/tests/browser/oauth.test.mjs` passed five deterministic
PKCE tests: actual SHA-256 challenge, state, expiration, config binding, callback
replay, URL cleanup and memory-only tokens. `node --check` passed for the browser
application. Forms are connected to the tested authenticated HTTP endpoints;
no live Cognito browser login or visual browser automation is claimed.

AC5: `custom/tests/test_platform_web.py` checks fixed asset serving, CSP and the two
Lambda roles' read-only access to platform grants. All 211 current custom Python
tests passed from the repository root with `-c setup.cfg -n0 custom/tests` (includes
concurrent organization work). Targeted flake8 and Linux mypy passed. The SAM
source passes cfn-lint; aws-sam-translator translates it to 29 CloudFormation
resources with only in-memory placeholder S3 CodeUri substitutions, no AWS calls.
The custom wheel builds offline. An extracted-wheel smoke check serves all five
browser assets and imports the Lambda handler without importing the core package
or creating AWS clients. Linux Python 3.12 container packaging remains unrun.

No AWS credentials, role grants, invitation emails or live resources have been
created. These are local, uncommitted changes. The full repository documentation check, custom links, tracker validation and
whitespace checks passed. The deployed IAM/audit/private-data boundary remains
unverified.

The role policy and ownership transfer implementation is in
[SPEC-0010](2026-10-29-company-roles.md). Company Admins invite ordinary Members;
Owner/Superman authority is required to invite company administrators. Initial
company provisioning assigns Company Owner explicitly.

## Next steps

Build Linux Python 3.12 SAM artifacts and run live login, invitation, audit,
backup/restore and isolation tests before production. After deployment, a trusted
operator must seed and audit the first Superman identity; no identity is inferred
from the current developer. Continue agent/memory and worker integration under the
existing specs. Company-local memberships are independent grants and require their
own revocation when removing all access from a former platform operator.
