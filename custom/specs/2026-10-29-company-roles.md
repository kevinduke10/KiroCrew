# SPEC-0010: Company and organization roles

## Intent

Implement the user-selected Company Owner, Company Admin, Organization Admin and
Member roles in the custom SaaS layer. Superman remains a separate platform role.
Owning custom contracts: [company workspaces](2026-10-29-company-workspaces.md),
[platform administration](2026-10-29-platform-administration.md) and
[organization scope](2026-10-29-organizations.md). Core governance is unchanged.

## Acceptance criteria

- [x] SPEC-0010-AC1: Persist and validate explicit scoped role values. Legacy company administrator booleans map to Company Admin, never Company Owner; legacy ordinary/organization memberships map to Member. Unknown or contradictory roles deny access.
- [x] SPEC-0010-AC2: New companies have one Company Owner. Owner transfer is atomic, retains a replacement owner and demotes the former owner to Company Admin. Owner/admin mutations and revocation require current server authority and conditional writes; the sole owner cannot be removed through an ordinary membership edit.
- [x] SPEC-0010-AC3: Owners appoint company administrators; Company Admins invite/manage ordinary Members and administer company organizations. Organization Admins manage Member assignments/groups only in their exact organization and cannot promote themselves or alter peer administrators. Members have no administrative rights.
- [x] SPEC-0010-AC4: Company/organization role management and invitations expose role selectors in the browser/API, use audited mutation paths and preserve private-content auditing and Superman separation. Roles do not imply ordinary agent visibility outside exact organization/resource grants.
- [x] SPEC-0010-AC5: Offline tests cover every role, owner transfer/revocation, privilege escalation, company/organization boundaries, persisted record compatibility and HTTP payloads. Docs, tracker and deployment permissions match the implementation.

## Implementation

Company memberships accept company_owner, company_admin or member. Organization
memberships accept organization_admin or member. The existing boolean is retained
as a validated compatibility field; authorization uses the normalized role. Missing
roles in legacy records never create ownership. A trusted Superman bootstrap may
appoint an initial owner for a legacy company lacking one.

A company record's owner identity and revision serialize ownership/role changes in
one DynamoDB transaction. Active company membership is required for role changes;
identity always comes from verified Cognito issuer/subject. Owner changes require
an existing active target company member. Company Admins can deactivate/reactivate
ordinary members, but cannot change company administrators/owners. Inviting an
administrator is reserved to Owner/Superman. Invitation cannot itself grant Owner;
invite first, then transfer ownership explicitly. Owner can rename company settings;
platform suspension/reactivation remains Superman authority.

Organization Admin authority applies to a single existing organization. It grants
membership/group management, not private coworker content access, company roles,
sibling/child visibility or organization creation. Owner/Company Admin/Superman can
appoint organization administrators. Organization membership never creates a company
membership. Pending billing, agent editing and execution surfaces must use these
roles when implemented; this change does not invent billing or agent-management APIs.

Role checks are fresh per operation. Concurrent in-flight operations are not
cancelled automatically. Global role revocation remains independent of company
memberships. All new role mutations use intent/outcome audit and fail closed.

## Validation

AC1–AC5: `custom/tests/test_company_roles.py` covers role persistence/legacy
mapping, scope rejection, owner bootstrap/transfer, last-owner protection, stale
revisions, administrator/member boundaries, exact Organization Admin authority,
private-content denials, role revocation, HTTP requests, audit metadata/failure and
native botocore validation of the owner-transfer transaction. Existing permission,
private-audit and organization tests also pass with explicit normalized roles.

Observed: 235 targeted Python tests passed with the concurrent knowledge tests
excluded during their collection repair. A subsequent complete root-configured run
`python -m pytest -c setup.cfg -n0 custom/tests` passed all 278 current tests,
including that knowledge work. Node browser checks passed all 11 tests, including
fresh Organization Admin permission controls and clearing rights on scope changes.
The app/organization modules pass Node syntax checks.

Targeted flake8, Black/isort and Linux-platform mypy passed for the top-level SaaS
modules and touched tests. The final broader Linux-platform mypy run also passes
for all 22 SaaS source files, including concurrent knowledge work. The SAM source passes cfn-lint and translates offline to
42 CloudFormation resources with placeholder S3 CodeUri values substituted only in
memory. The custom wheel builds offline, contains browser assets identical to the
current source, and imports the role model/serves the administration shell without
core imports or AWS clients. The repository documentation gate passed.

No AWS accounts/resources, real role grants, invitations, commit or deployment were
created. Linux Python 3.12 SAM/container packaging and deployed IAM behavior are
not verified by these offline tests.

## Next steps

Use the roles for future billing/agent/worker surfaces and verify the deployed
permission matrix before production. After deployment, trusted Superman authority
must explicitly assign an initial Owner for legacy companies without one; no
existing admin is promoted automatically. Keep company role changes separate from
platform grant bootstrap/revocation.
