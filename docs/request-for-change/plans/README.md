# Implementation Plans

Dated, task-by-task execution plans for documents in
[../](../README.md). A plan is the *how*; its RFC stays the *what* and the *why*.
An RFC plan names its RFC as its spec, so the two are read together.
Feature plans can instead link the owning module specs and record the user intent
and acceptance criteria under the [spec-driven workflow](../../system-specs/common/spec-driven-development.md).

Checkbox state is a progress record, not a status: read the RFC's `status` for
that, and read the **State** column here for whether a plan is being worked.

| Plan | Spec | State |
|---|---|---|
| [2026-08-22-durable-run-coordinator.md](2026-08-22-durable-run-coordinator.md) | [rfc-durable-run-coordinator.md](../rfc-durable-run-coordinator.md) | **Obsolete.** 0 of 52 checklist steps are done. The RFC is superseded by `rfc-overload-resilience.md`; its durable store now ships as `src/kiro_crew/taskq/`. |
| [2026-08-27-agentcore-identity-gateway.md](2026-08-27-agentcore-identity-gateway.md) | [rfc-agentcore-identity-gateway.md](../rfc-agentcore-identity-gateway.md) | **Partial on main.** 4 of 30 checklist steps are marked done, but current code contains only the AWS-free core seam (`platform/agentcore_schema.py`, `AgentIdentityProvider`, public Default, and governance row); later AWS/IAM/Gateway work is absent. |
| [2026-09-23-outbound-instance-transport.md](2026-09-23-outbound-instance-transport.md) | [rfc-outbound-instance-transport.md](../rfc-outbound-instance-transport.md) | **Unstarted.** 0 of 6 phases done at `18f9984b0`: `src/kiro_crew/instances/transports/` has zero hits and `CONNECTION_METHODS` carries no `outbound` entry. Phase 2 is a pure refactor of a seam three methods already branch on, and must land green before phase 3 begins. |

Indexed from [../README.md](../README.md).

## Tracked feature work

Current status and checkpoint history live in [work-tracker.json](work-tracker.json).
Use `python3 scripts/spec_tracker.py list` or `history SPEC-NNNN` from the checkout;
read the linked plan for acceptance criteria, evidence and the next step. These
records include uncommitted work and do not claim a merge or release.

| Feature | Plan |
|---|---|
| SPEC-0001 | [Native Bedrock without an agent CLI](2026-10-29-native-bedrock.md) |
| SPEC-0002 | [Persistent spec-driven development workflow](2026-10-29-spec-workflow.md) |
