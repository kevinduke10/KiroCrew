# SPEC-0009: Organization hierarchy and agent visibility

## Intent

Implement the organization requirements of [SPEC-0004](2026-10-29-company-workspaces.md)
in the pooled API. This spec owns organization records, management endpoints and
organization-aware authorization; [SPEC-0006](2026-10-29-tenant-authorization.md)
and [SPEC-0007](2026-10-29-pooled-api.md) own the existing access and API seams.

## Acceptance criteria

- [x] SPEC-0009-AC1: Company administrators can create root and nested organizations with immutable same-company parents, bounded validation and conditional duplicate protection. Missing parents, cycles and cross-company parents are refused.
- [x] SPEC-0009-AC2: Company administrators can explicitly assign/revoke organization memberships for existing company members. Organization membership cannot create a company administrator or infer membership from ancestry.
- [x] SPEC-0009-AC3: Resource keys, metadata and API routes carry exact organization scope. Current company and organization memberships plus existing grants gate ordinary read/edit/run; missing scope fails closed.
- [x] SPEC-0009-AC4: A bounded agent directory returns only explicitly readable agents in the selected organization, with no private administrator exception for discovery. Guessed IDs and revoked memberships cannot bypass checks.
- [x] SPEC-0009-AC5: Offline tests cover hierarchy, unauthorized writes, group scope, revocation and parent/child/sibling/company visibility, with valid SDK request shapes and deployment routing/IAM checks.

- [x] SPEC-0009-AC6: The signed-in web administration shell lets a company administrator create organizations/sub-organizations and assign/revoke members; organization selection lists only authorized agents and clears data on company changes.

## Implementation

Keep changes under custom/. Extend membership records with optional organization
scope (empty means company membership only). Resource scope is mandatory. Separate
organization membership records contain their own group grants. Company-admin
status comes exclusively from the company record. Organization creation and
membership management use authenticated API endpoints; company provisioning stays
trusted and out of this API. Parent links cannot be changed or deleted by this API,
so conditional insert after validating an existing parent cannot create a cycle.

The unshipped company-only resource routes are replaced by organization-qualified
routes. Old records are never auto-assigned to an organization. Trusted provisioning
must rewrite them with explicit scope and a fresh revision before they are usable.
The current gateway has no callers of this separate API. Update all custom fixtures,
SAM routes and authoring examples together. No AWS deployment is part of this work.
Agent execution workers and streams remain unbuilt. The organization panel connects
to the browser shell supplied by SPEC-0008; customer agent scope is enforced on
the server. The separate audited platform-support READ/EDIT exception remains;
it never substitutes for organization membership in ordinary agent discovery.

The SPEC-0008 integration supplies a trusted platform-aware administrative check
when composed in production, and audits organization mutations before execution.
Superman is an explicit platform exception; ordinary membership and discovery
remain scoped to the exact organization. Company onboarding creates the root
organization and its initial membership in one transaction.

[SPEC-0010](2026-10-29-company-roles.md) adds Organization Admin membership and
fresh permission lookup. Organization Admin can manage Member assignments/groups
only in its exact organization; company administrators appoint organization
administrators. Owner and platform authority remain separate from resource grants.

## Validation

AC1–AC5: `custom/tests/test_saas_organizations.py` exercises nested creation,
duplicate protection, malformed/cyclic/foreign parents, bounded traversal,
unauthorized writes, explicit assignment/revocation, exact-organization read/run
and directory visibility, independent group grants, legacy-scope refusal,
metadata-only paging, capacity failure and revocation during listing. Botocore
Stubber validates scoped membership writes, organization creation and Query requests.
`test_saas_template.py` pins routes and the `ORG#*` write partition fence.
`test_organization_platform_boundary.py` pins suspension and prevents audited
platform support from becoming ordinary organization-directory membership.

AC6: `node --test custom/tests/browser/*.test.mjs` passed 10 tests, including five
organization UI tests for form payloads, root/child creation, assignment/revocation,
ordinary-member controls, inert customer text and stale-response/company-switch
isolation. A real in-app browser rendered the organization panel through a
loopback-only fixture server and exercised creation of Recruiting under HR and
loading the Recruiting agent list. The fixture used fake responses, not live AWS.
The preview server was stopped after verification.

`/private/tmp/kirocrew-bedrock-venv/bin/python -m pytest -c setup.cfg -n0 custom/tests`
passed 209 tests in the combined local tree, including concurrent platform work.
Targeted flake8, Black/isort, Linux-platform mypy, cfn-lint and tracker checks passed.
The repository documentation gate passed with existing report-only identifier notes.
No live AWS IAM/isolation validation, deployment, real invitations or commit is claimed.
Membership checks are request-time checks, not atomic with concurrent revocation;
worker cancellation and streaming remain outside the currently implemented runtime.

## Next steps

Build Linux SAM artifacts and verify Cognito, DynamoDB/IAM and isolation in an
authorized test deployment. Carry organization scope into future workers, streams
and memory before enabling those features. Existing records require explicit trusted
organization assignment; no broad legacy fallback exists.
