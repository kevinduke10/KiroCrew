# SPEC-0005: Lower-cost company-isolated pilot

## Intent

Status: the per-company EC2 pilot is deferred. On 2026-10-29 the user selected
pooled serverless with logical isolation as the starting configuration. The original
pilot requirements below remain as a historical alternative, not current delivery work.

The user rejected the USD 150–300/company/month illustrative dedicated stack as
too expensive and requested a cheaper starting design. Preserve the company AWS
isolation and audited private-space administrator access from
[SPEC-0004](2026-10-29-company-workspaces.md). Reduce fixed infrastructure costs
rather than sharing customer runtimes or storage across accounts.

The proposed profile is [lean-pilot.json](../deploy/lean-pilot.json). It is a
non-deployable design document, not an implemented configuration or an instruction
to launch AWS resources. No company deployment or memory benchmark exists yet.

## Acceptance criteria

- [x] SPEC-0005-AC1: A concrete pilot design and reproducible cost worksheet preserve one AWS account per company while removing unnecessary per-company fixed-cost services.
- [ ] SPEC-0005-AC2: The minimum Bedrock-only application profile is implemented under custom/ and measured before selecting a smaller host; the full upstream workload's memory floor is not silently overridden.
- [ ] SPEC-0005-AC3: Load tests demonstrate memory/CPU headroom and bounded queuing for the proposed pilot workload without disabling company or space authorization.
- [ ] SPEC-0005-AC4: Persistent data survives instance replacement, and application-consistent backup/restore tests demonstrate the agreed recovery targets.
- [ ] SPEC-0005-AC5: Deployment tests verify company account isolation, TLS-only application access, non-public database/gateway ports, credential separation and durable privileged-access auditing.
- [ ] SPEC-0005-AC6: A measured AWS bill and usage profile support the advertised operating budget, with inference and optional-service costs identified separately.

## Implementation

### Proposed pilot topology

One company account contains one EC2 instance, encrypted EBS volumes, its own
backup destination, permissions and audit sink. A TLS reverse proxy serves the
company hostname directly; only HTTPS and certificate/redirect HTTP are public.
The gateway binds to loopback. SQLite, history and memory stores use local block
storage, and only trusted services can read the complete backing stores. Membership
and space checks govern access; agent tools do not inherit full host access.

A public subnet plus a public IPv4 address and Internet Gateway provides outbound
access without a billable NAT gateway. Do not expose the gateway's raw port,
database ports, SSH or the instance metadata service to untrusted agent tools.
Use Systems Manager for host administration and an EC2 instance profile for trusted
services' temporary AWS credentials. This does not add customer-to-customer access.

The pilot omits a managed load balancer, NAT gateway, RDS, an external vector
cluster and Kubernetes. Application-consistent backups, privilege audit, TLS,
company isolation and space authorization remain required. Avoiding managed services
moves patching, database recovery and reverse-proxy operations onto our software.
Do not trade away those duties to meet a price target.

### Runtime and sizing gate

Upstream `src/kiro_crew/cloud/sizes.py` documents roughly 10 GB RAM with spikes
for the full Kiro Crew workload and offers a 16 GB minimum. Our Bedrock runtime
avoids an external agent CLI, but no measurement proves a 4 GB SaaS host adequate.
The earlier generic Fargate budget likewise was not a Kiro Crew memory benchmark.

The candidate is `t3a.medium` (2 burstable vCPUs, 4 GiB), with a queued pilot cap of
one active agent turn per company. This is a proposed admission rule, not an
existing config key or a capacity claim about the number of users. Queue extra
work instead of spawning unbounded processes. Keep one embedding worker; the
existing local embedding footprint must be included in the measurement. Browser,
media and parallel build workloads are outside this pilot's performance baseline.

First measure the existing gateway with the native Bedrock path and only required
services. If it does not fit, identify the actual memory owners and implement an
explicit custom startup composition; do not merely lower upstream's size floor.
Do not use swap or turn off privacy/security gates as proof of adequate memory.

Use CPU-credit Standard mode for the budget model: exhausted credits throttle
bursts rather than adding unlimited-mode credit charges. A load test must determine
whether that behavior meets the agreed latency target. No Spot interruption risk,
free-tier credit or long-term purchase commitment is assumed in these estimates.

Sizing evidence must cover warm startup, idle, active tools, history/memory growth,
embedding/indexing, queued turns, backups and recovery. A candidate passes only with
measured headroom; document the load and data volume rather than inventing a user
capacity. If 4 GB fails, evaluate 8 GB, then the existing 16 GB floor. If none meets
cost and performance requirements, revisit the runtime rather than deploy an
undersized host.

### Persistence, recovery and private data

Keep data on a separately retained encrypted EBS volume, not a container's
writable layer. Back up SQLite databases with the online backup API or a coordinated
application quiesce; copying live database files without their WAL is not sufficient.
Include membership, ACLs, history, memory, artifacts and encryption dependencies in
the recovery procedure. Store customer-content backups in that company's account.

Proposed recovery targets are a 15-minute recovery point and a 60-minute recovery
time; both require implementation, agreement and restore testing. A single host
has maintenance and recovery downtime and is not a high-availability deployment.
Backup retention, audit volume and key access must be costed against actual data.

Private-space reads by company administrators must record durable access events
before disclosure. Keep audit credentials/permissions separate from agent execution
and prevent routine application users from deleting audit records. This is a
required SaaS feature, not a guarantee of the current upstream or native runtime.

### Cost worksheet

Rates checked 2026-10-29: US East (N. Virginia), Linux/x86 on-demand, 730 hours/month,
40 GiB gp3 total, one public IPv4, light load and up to 10 GiB customer data.
The backup and auxiliary lines are allowances, not quoted AWS fixed fees.

| Component | USD/company/month |
|---|---:|
| t3a.medium compute: 0.0376 × 730 | 27.45 |
| One public IPv4: 0.005 × 730 | 3.65 |
| 40 GiB gp3: 0.08 × 40 | 3.20 |
| Backup/snapshot allowance | 2–6 |
| Logs, DNS and keys allowance | 3–8 |
| Illustrative total | 39.30–48.30 |

The target is approximately USD 40–50/company/month for the 4 GB candidate,
subject to the sizing gate. The same worksheet gives approximately USD 67–76
for an 8 GB t3a.large and USD 122–131 for a 16 GB t3a.xlarge. A dedicated account
adds no AWS Organizations service fee. Separate accounts do not require ALB/NAT/RDS.

Exclude Bedrock inference, significant network egress, heavy backup/audit volume,
shared provisioning/control-plane costs, paid identity-provider/security/support
plans, taxes and engineering/on-call labor. Costs vary with workload and Region;
this is not a measured bill, subscription price or availability commitment.

Rate and design references:

- [EC2 T3a prices](https://aws.amazon.com/ec2/instance-types/t3/)
- [EBS prices](https://aws.amazon.com/ebs/pricing/)
- [Public IPv4 prices](https://aws.amazon.com/vpc/pricing/)
- [Internet Gateway routing](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Internet_Gateway.html)
- [EC2 application roles](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html)
- [CPU-credit behavior](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-performance-instances-unlimited-mode-concepts.html)

### Delivery order

1. Establish the minimum native Bedrock startup profile and benchmark it locally in
   a disposable data home, with mocked inference and no live AWS credentials.
2. Implement/test company and space authorization for the SaaS slice independently
   of infrastructure cost optimizations.
3. Add a custom infrastructure template for the smallest measured host, along with
   queued execution, backups, audit delivery, TLS and readiness checks.
4. Only after explicit deployment authorization, launch one test company account,
   exercise failure/recovery and compare the bill with this worksheet.


### Shared serverless starting design (selected)

After reviewing the estimate, the user selected logical isolation with shared
managed/serverless services and deferred dedicated AWS accounts until needed. Cross-company content sharing remains prohibited in either
architecture. The scenario and arithmetic live in
[pooled-serverless-estimate.json](../deploy/pooled-serverless-estimate.json).

Proposed pooled stack: S3/CloudFront frontend, Cognito sign-in, API Gateway and
Lambda APIs, DynamoDB on-demand records, S3 documents and a company/space-scoped
managed vector store such as S3 Vectors. Use asynchronous jobs with Lambda for
short bounded work and on-demand Fargate for longer/untrusted tool executions.
Avoid a dedicated NAT gateway, managed load balancer, relational database or
always-on agent server per company in this scenario.

For each company, assume 10 monthly active users, 1,000 jobs/month averaging
2 GiB and 120 billed seconds, 50,000 small browser API calls, 1 GB records,
1 GB documents and a small memory index. US East rates, no promotional credits
or free tiers subtracted. The worker-compute yardstick is
1,000 × 2 × 120 × USD 0.0000166667 = approximately USD 4/month/company.
Waiting on inference still counts toward billed execution time.

Budget USD 6–15/company/month plus USD 20–60/month shared platform overhead,
excluding generation/embedding inference, large retrieval/transfer workloads,
advanced paid plans and engineering labor. These are planning allowances, not
AWS minimum charges or a measured application bill. The resulting totals are
USD 80–210 for 10 companies, USD 620–1,560 for 100, and USD 6,020–15,060 for 1,000.
A company with 10,000 jobs at the same size/duration incurs approximately USD 40
in worker compute alone; the small-company allowance does not apply unchanged.

Ordinary Lambda invocations have a 15-minute ceiling; the final agent execution
mix must be priced against actual task durations and isolation requirements.
Fargate has its own startup, networking and execution charges. The estimate does
not assume the upstream gateway/local SQLite filesystem can run in Lambda unchanged.
A serverless adaptation and persistent authorization-scoped data model are
required. Authentication, metadata filters or a tenant ID alone are not proof
that private-space or cross-company isolation is enforced.

## Validation

Observed local checks passed: profile JSON parsing and isolation/topology assertions,
recomputed monthly amounts for all three host sizes, custom Markdown link checks,
`python3 custom/scripts/spec_tracker.py check --base HEAD` and `git diff --check`. No new runtime behavior is
implemented and no AWS resources are created. Existing unit tests do not establish
4 GB capacity, data recovery, private-space isolation or production readiness.

## Next steps

Proceed with the pooled authorization foundation in
[SPEC-0006](2026-10-29-tenant-authorization.md), then verified identity, persistence
and deployment templates. Resume the EC2 benchmark only if this alternative is
selected later. No AWS deployment is authorized by this design selection.
