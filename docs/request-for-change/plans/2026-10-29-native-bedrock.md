# SPEC-0001: Native Bedrock without an agent CLI

## Intent

The user requested that Kiro Crew use any AWS Bedrock model and remove the CLI
dependency entirely. Development and offline verification must not require AWS
credentials. The implemented increment selects a bundled native backend through
`agent.acp_backend`; the broad model-coverage requirement remains open.

Owning specs: [providers](../../system-specs/modules/providers.md),
[ACP client](../../system-specs/modules/acp-client.md),
[model selection](../../system-specs/common/model-selection.md), and
[host contract](../../system-specs/modules/agent-host-contract.md).

## Acceptance criteria

- [x] SPEC-0001-AC1: The `bedrock` backend launches and initializes through the current Python interpreter without kiro-cli, AWS CLI or another agent executable.
- [x] SPEC-0001-AC2: An explicit Converse model ID or inference-profile ARN passes through configuration to the native backend; there is no hardcoded model fallback.
- [x] SPEC-0001-AC3: Projected MCP calls retain the Crew permission gate, and interrupted turns retain completed tool results without automatically repeating side effects.
- [x] SPEC-0001-AC4: Mocked tests and initialization work without AWS credentials or live AWS inference.
- [ ] SPEC-0001-AC5: Model coverage meets the original “any AWS Bedrock model” request, including an explicit disposition for models requiring APIs other than Converse.

## Implementation

The current uncommitted increment adds `src/kiro_crew/bedrock_runtime/`, its ACP
launch adapter and mirror, backend registry declarations, optional SDK dependencies,
and Bedrock identifier validation. Existing optional CLI backends remain selectable.

The runtime accepts text and uses boto3 Converse plus session-projected MCP tools.
Token streaming, native session restore, model discovery and non-Converse APIs
are absent. Image/embedding models do not fit the current chat contract. These
are recorded implementation limits, not a user-approved narrowing of the request.

Tasks:

- [x] Implement the native runtime and adapt it to the existing ACP seams.
- [x] Add offline tests and configuration/install documentation.
- [ ] Define which additional model/API capabilities are required for AC5.
- [ ] Implement and validate the agreed additional coverage.
- [ ] Review the consolidated diff and commit only when the user asks.

## Validation

Evidence carried forward from the implementation chat; the commands below are
reported completed checks from that work, not fresh results from tracker setup:

- AC1: Native process smoke check initialized ACP and created an empty-tool session
  with a minimal PATH and no AWS credentials.
- AC2: `test/test_qualified_model_ids.py` and `test/test_bedrock_runtime.py` cover
  versioned IDs, profile/foundation ARNs, validation and provider construction.
- AC3: `test/test_bedrock_runtime.py` covers permission allow/deny, unknown tools,
  cancellation, step limits and preserving completed results. Harness parity gates pass.
- AC4: Targeted offline runs reported 354 and 382 passing tests respectively;
  their selections overlap and must not be added together. New runtime modules
  passed targeted mypy checks with `--platform linux`; formatting, lint, line-cap
  and documentation checks passed. No live AWS inference was tested.
- AC5: Unverified and incomplete. Current implementation uses Converse only.

Reproduce the focused runtime checks with:

```bash
python3 -m pytest -n0 test/test_bedrock_runtime.py test/test_qualified_model_ids.py
HARNESS_BASE_REF=origin/main python3 scripts/check_harness_parity.py
```

The full repository gate was not run; its dry-run selected thousands of test files.
There is no commit, PR or release reference for this feature yet.

## Next steps

Resolve AC5 coverage against the original request, then implement the remaining
agreed behavior. Keep AWS credentials out of offline development. Live AWS testing
is optional integration evidence, not a prerequisite for writing or mocking code.
