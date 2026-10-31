# SPEC-0004: Isolated company workspaces for a business SaaS

## Intent

Build a SaaS where a company can create groups such as HR, share agents and memory
within those groups, see authorized shared work, and maintain private agent spaces.
The user explicitly prohibits cross-company sharing. On 2026-10-29 the user
selected logical isolation in shared managed/serverless infrastructure, superseding
the earlier requirement for a dedicated AWS environment per company. Company administrators may access private
conversations and memory with an audit trail. These are confirmed requirements. Companies must also support organizations with
nested sub-organizations; users can see agents only in their own organization.

This is an in-progress product and architecture spec, not an implemented SaaS.
Dedicated AWS accounts are a future deployment option. Authorization contracts are tracked in [SPEC-0006](2026-10-29-tenant-authorization.md);
Cognito, persistent adapters and a guarded API are in
[SPEC-0007](2026-10-29-pooled-api.md).

Relevant upstream contracts:
[Shared Crew](../../docs/system-specs/modules/shared-crew.md),
[memory](../../docs/system-specs/modules/memory-skills-hooks.md),
[dashboard authentication](../../docs/system-specs/modules/dashboard-token-auth.md),
[platform seams](../../docs/system-specs/modules/platform-context.md), and
[security boundaries](../../docs/architecture/security-deep-dive.md).
Upstream Shared Crew is attribution/fairness rather than per-person access control;
member memory is explicitly not an adversarial same-host confidentiality boundary.
Neither is sufficient by itself for this SaaS privacy model.

The user also requires a Superman platform role that administers all companies.
[SPEC-0008](2026-10-29-platform-administration.md) implements this explicit audited
operator exception. It does not let ordinary company principals share content or
grant themselves platform authority. Browser login and controlled company
onboarding are implemented in that separate administration shell.

The company roles are Company Owner, Company Admin, Organization Admin (scoped to
one organization) and Member, as implemented in
[SPEC-0010](2026-10-29-company-roles.md). Superman remains platform-wide.

## Acceptance criteria

- [ ] SPEC-0004-AC1: Each company has a stable identity with logical isolation in pooled infrastructure; another company's principals and workloads cannot access its resources or content. Dedicated accounts remain optional.
- [ ] SPEC-0004-AC2: Company onboarding creates and verifies tenant membership, storage scope and deployment placement before becoming available, with resumable status, bounded retries and duplicate-request protection; it does not require creating an AWS account.
- [ ] SPEC-0004-AC3: Users authenticate to a company and receive server-validated company membership; browser-supplied IDs alone never establish access.
- [ ] SPEC-0004-AC4: A company can create arbitrary internal groups and manage their membership and permissions without creating a new customer tenant for every group.
- [ ] SPEC-0004-AC5: Group members can use explicitly shared agent definitions, with independent grants for running, editing and managing them.
- [ ] SPEC-0004-AC6: Shared memory is scoped to its company and group; retrieval, embeddings, caches, summaries, tools, jobs and exports cannot bypass that scope.
- [ ] SPEC-0004-AC7: Private spaces exclude ordinary coworkers; authorized company administrators can access their content only through a durably audited privileged path.
- [ ] SPEC-0004-AC8: Sharing an agent does not implicitly share its users' conversations, credentials, private memory or artifacts.
- [ ] SPEC-0004-AC9: Shared activity shows only authorized work status and deliberately shared details; private titles, prompts, summaries and tool output cannot leak through listings or notifications.
- [ ] SPEC-0004-AC10: Membership and permission revocations apply to ongoing sessions, event streams, queued work and subsequent tool execution, with no reuse of another space's model context.
- [ ] SPEC-0004-AC11: Cross-company agent, memory, conversation, artifact and activity sharing is unavailable, including through API calls or indirect agent delegation.
- [ ] SPEC-0004-AC12: Automated tests cover company and space isolation, administrator auditing, direct-object access, background jobs and revocation; deployment-level evidence verifies AWS policy boundaries before production use.

- [x] SPEC-0004-AC13: Authorized administrators can create organizations and nested sub-organizations within a company. Every parent exists in the same company; self-parenting, cycles and cross-company parenting are rejected. Creation and membership administration require server-validated authority.
- [ ] SPEC-0004-AC14: Every agent belongs to one organization. Agent discovery, names, counts, search, direct reads and execution require current membership in that exact organization plus applicable resource grants. Parent, child and sibling relationships grant no implicit visibility.
- [ ] SPEC-0004-AC15: Organization scope survives agent jobs, delegation, events and caches; revoked membership denies subsequent access and delivery. Tests cover root, child, grandchild, sibling, unrelated organization and unrelated company boundaries, including direct-ID requests.

## Implementation

### Company isolation

Start with S3/CloudFront, Cognito, API Gateway/Lambda, DynamoDB on-demand,
S3 documents, scoped managed vector retrieval and queued workers. The selected
non-deployable cost scenario is
[pooled-serverless-estimate.json](../deploy/pooled-serverless-estimate.json).
No always-on worker, NAT gateway, load balancer or relational database is required
per company. Long or untrusted tool work needs separately scoped workers; this
is not permission to run arbitrary tools inside the shared API process.

Every content record, object, index query, job and event carries company and space
scope. Server-side membership and resource authorization apply before disclosure
or execution, including listings and background delivery. Tenant IDs supplied by a
browser are selectors only. Shared infrastructure is an accepted starting tradeoff;
logical isolation is not the same boundary as separate AWS accounts.

Use stable company IDs independent of AWS placement. A future placement directory
can route a company to dedicated storage/runtime/account resources without changing
its identity or access rules. Migration will require data copy, scoped credentials,
cutover, rollback and isolation verification; that machinery is not implemented.

### Organizations and sub-organizations

The company remains the outer tenant boundary. Inside it, organizations form a
hierarchy with stable IDs and optional parent organization IDs. Sub-organizations
can contain further sub-organizations. These are application records, not AWS
Organizations accounts. Trusted company onboarding creates a root organization;
authorized organization management creates descendants only within the same company.
Creation validates parent existence, administrative authority and an acyclic tree.

Agent visibility is confined to the exact organization of the current membership.
A parent relationship is structural and does not inherit agent visibility upward,
downward or sideways. If multiple organization memberships are supported, the user
selects one authorized organization context; agent lists never union the entire
company tree. Membership alone does not reveal private or ungranted agents.

Groups and spaces belong to an organization within their company. Each agent carries
company, organization and space scope. Server-side authorization must validate all
three before returning even an agent name, count or existence signal, and before
running an agent. Search, direct URLs, jobs, delegation, events and caches use the
same scope. Browser filtering is insufficient. Existing company-only records need
an explicit trusted migration; missing organization scope must not imply access to
all organizations. Reparenting and cross-organization agent moves remain outside
initial creation scope and must not silently broaden visibility.

The existing audited company-administrator private-content requirement remains a
separate privileged access path; it does not populate ordinary agent directories
or grant cross-organization execution. Organization membership is still required
for ordinary agent visibility. Backend implementation is tracked in SPEC-0009;
browser management and deployed isolation verification remain separate work.

### Internal groups and private spaces

Each organization contains shared and private spaces. A group such as HR grants
membership in one or more spaces within that organization; it is not a separate AWS customer account. A private
space belongs to its owner, with the explicitly authorized administrator exception.

Agent definitions, agent executions, memory stores, conversations and activity are
separate resources. Publishing an agent definition does not publish its sessions
or memory. A run has a captured company, space, requester, agent version and policy
context. Tools, model history and memory retrieval retain this context, and grants
are rechecked when executing or delivering results.

Private-space protection cannot rely on UI filters or prompt instructions. Runtime
filesystem mounts, tool brokers and storage permissions must prevent another space
from reading the underlying private content. Reusing one unrestricted upstream
memory directory across coworkers does not satisfy this contract.

### Administrator access and audit

The administrator exception is limited to authorized administrators of the same
company. A privileged read records the actor, company, target space/resource,
action, timestamp and outcome before disclosing content; audit failure refuses the
privileged read. The application cannot let an administrator erase their own access
history. Administrative access does not republish private data into group feeds,
shared agent memory or ordinary search. Support/operator access is designed and
audited separately. Whether owners receive access notifications remains open.

### Proposed delivery sequence

1. Implement and test company/space authorization contracts offline (SPEC-0006).
2. Integrate verified Cognito identity, authoritative memberships, DynamoDB records
   and a durable audit sink; implement idempotent pooled tenant onboarding.
3. Add organization/sub-organization creation and authoritative memberships, then
   organization-scoped agent definitions, memory, documents and queued workers.
4. Add filtered activity and prove revocation across requests, streams and jobs.
5. Deploy a test stack only after deployment authorization and verify isolation.

All product implementation belongs under `custom/`, using documented core seams.
The SaaS login system must not revive internal enterprise-SSO implementations.
OIDC federation and its provider selection are future design decisions, not
features supplied by the current public build.

### Future dedicated-account onboarding option

AWS Organizations can create member accounts programmatically. Account creation is
asynchronous: persist the request ID and reconcile its result before deploying the
company stack. Use a controlled unique account email, an automated infrastructure
template and readiness checks. Do not blindly repeat a creation request after a
network timeout. A pending company remains unavailable until deployment and
isolation checks pass. Failure must leave a recoverable provisioning record.

AWS account quotas, provisioning concurrency, per-company infrastructure cost,
updates, suspension and account closure are operational requirements of this
approach. Exact service limits are read from current AWS documentation and the
operator's actual quotas, rather than embedded as product constants here.

Future account-isolation choices: provider-owned versus customer-owned accounts.
Open pooled deployment choices: Region/data residency; initial company-login
provider; deployment sizing and cost; account lifecycle and deletion/backup policy.


### Initial operating-cost envelope (comparison baseline)

The user rejected this initial envelope as too high. The lower-cost pilot design
is retained in [SPEC-0005](2026-10-29-lean-company-pilot.md) as a deferred
alternative. The pooled serverless comparison is now the selected starting design.

Planning estimate as of 2026-10-29, not a measured bill or deployment quote:
US East (N. Virginia), 730 hours/month, Linux/x86 Fargate, light traffic,
one always-on worker (1 vCPU/4 GiB through 2 vCPU/8 GiB), a small single-AZ
database, one ALB and one NAT gateway. No high-availability worker/database
configuration is implied.

| Component | Estimated USD/company/month |
|---|---:|
| Always-on Fargate worker | 43–85 |
| ALB, base plus light capacity | 16–25 |
| NAT gateway, base plus light processing | 33–40 |
| Small database/storage allowance, not a quoted instance price | 25–80 |
| IPv4, logs, backups, keys and light auxiliary usage allowance | 20–40 |

Round to a planning budget of approximately USD 150–300/company/month before
Bedrock inference, burst agent workers, significant egress, shared control-plane
cost, paid security/support products, taxes and engineering/on-call labor.
AWS Organizations itself has no additional service charge; duplicated running
resources drive the estimate. Real concurrency, memory usage and retained data
must be measured before choosing instance sizes or committing customer pricing.

This is the total illustrative dedicated-company stack, not all incremental
isolation overhead. For a hypothetical pooled baseline of USD 30/company/month,
the additional infrastructure would be roughly USD 120–270/company/month.
That pooled baseline is an assumption, not a measurement of this application.
A separate account does not require every service in this sample stack; lower
fixed-cost designs can retain account isolation but need their own reliability,
sizing and deployment review.

Rate references:
[Fargate](https://aws.amazon.com/fargate/pricing/),
[ALB](https://aws.amazon.com/elasticloadbalancing/pricing/),
[VPC/NAT and public IPv4](https://aws.amazon.com/vpc/pricing/), and
[Organizations billing](https://docs.aws.amazon.com/organizations/latest/userguide/pricing.html).

## Validation

No SaaS infrastructure has been provisioned. SPEC-0006 adds an offline authorization
library; SPEC-0007 composes it with Cognito/DynamoDB in a separate pooled API.
Neither is deployed or connected to the personal gateway. Organization creation (AC13) is verified locally in SPEC-0009; the other product
acceptance criteria remain open. Local API and browser checks are not production isolation evidence.

The local tracker/schema checker and custom Markdown links are checked when this
plan is recorded. Planned product tests include a company A/B and owner/member/admin
matrix across reads, writes, retrieval, listings, WebSockets, exports, background
jobs, AWS roles, storage mounts and revoked memberships.

Primary design references:

- [AWS account separation](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/aws-account-management-and-separation.html)
- [Silo SaaS isolation and tradeoffs](https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/silo-isolation.html)
- [AWS Organizations CreateAccount](https://docs.aws.amazon.com/organizations/latest/APIReference/API_CreateAccount.html)
- [Organizations quotas](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_reference_limits.html)
- [Account-per-tenant lifecycle](https://aws.amazon.com/blogs/mt/managing-the-account-lifecycle-in-account-per-tenant-saas-environments-on-aws/)

## Next steps

Continue SPEC-0007 with browser PKCE login and controlled company onboarding,
then organization/sub-organization creation, exact-organization agent visibility,
shared agents, scoped memory and deployment verification. Prove AWS policies and every content path before production. Dedicated
accounts can be added later; no cross-company sharing and audited administrator
access to private content remain required.
