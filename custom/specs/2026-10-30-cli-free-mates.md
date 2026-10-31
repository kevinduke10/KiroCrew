# SPEC-0013: CLI-free defaults, mates and explicit AI failures

## Intent

The user reported that creating a mate searched for kiro-cli despite the original
request to remove that dependency. The active config has an empty default backend,
a KAS member backend with its bundle disabled, and model auto. AWS is not configured
yet. Mate creation must work locally; AI use must fail with an actionable message,
not switch to a CLI. The user explicitly requests a cannot-reach-AI error on failure.

Owning contracts: [native Bedrock](2026-10-29-native-bedrock.md),
[config](../../docs/system-specs/modules/config.md),
[harness parity](../../docs/system-specs/modules/harness-parity.md),
[crew mode](../../docs/system-specs/modules/crew-mode.md),
[ACP client](../../docs/system-specs/modules/acp-client.md), and
[host contract](../../docs/system-specs/modules/agent-host-contract.md).

## Acceptance criteria

- [x] SPEC-0013-AC1: Fresh default and member sessions select native Bedrock. Invalid/unavailable selections raise a clear AI error instead of silently selecting Kiro CLI. Explicit optional legacy backends are not required for normal operation.
- [x] SPEC-0013-AC2: Bedrock member sessions carry verified saved-spec MCP projections and the per-session dispatch/worker kit through existing guarded seams. Unsupported tool-level capabilities remain refused, never silently widened.
- [x] SPEC-0013-AC3: A missing model, missing AWS setup or unreachable inference service returns a sanitized actionable AI error; it never launches an alternate harness. Initialization and local mate records need no AWS credentials.
- [ ] SPEC-0013-AC4: Offline regressions cover backend selection and member/worker capabilities, session projection and failed/missing-setup prompts with CLI resolvers disabled. Current config can be corrected without choosing a model or exposing credentials.

## Implementation

The fork deliberately selects Bedrock for DEFAULT_ACP_BACKEND and the member DTO
default. This supersedes the upstream KAS default for this fork by explicit user
instruction. Backend admission still applies; an unavailable pick raises rather
than selecting a different backend. Governance/sandbox enforcement is unchanged.
The named Kiro floor remains registry vocabulary for compatibility, but the
selection gate does not use it to recover a failed choice.

Bedrock joins the member projection/dispatch and worker-kit capability sets, with
prompt delivery through context and conservative restrictions. Its custom mirror
must pass session_key into the existing session_mcp_projection so worker intent
and the recorded consumed spec are the same. Do not claim native script hooks or
per-tool mount fidelity; unsupported restrictions continue to refuse/withhold.

The active local config needs both backend fields set to bedrock. Keep model auto
until the user chooses an actual model; no default model ID or credentials are
invented. Use the existing locked config writer and preserve unrelated settings.
The launcher currently resolves inside `/Applications/KiroCrew.app`, which is a
separate stock installed runtime. Source edits do not change that application.
Do not change its config to a backend that bundle does not contain. Install/rebuild
the fork first, then update both backend fields through the locked writer.
No gateway restart, host config edit, AWS provisioning or live inference is claimed.

## Validation

- AC1: `custom/tests/test_cli_free_mates.py` verifies native default/member/worker
  selection with CLI resolution forbidden, unavailable defaults and explicit picks,
  and full config loads with malformed/unknown ordinary and member backend fields.
- AC2: mirror forwarding and capability guards are pinned in the custom regression
  and core harness/member/worker tests. A real native ACP child and real MCP child
  completed one approved tool invocation in
  `test/fixtures/acp_frames/bedrock/native-mcp-transport-live.jsonl`; inference alone
  was mocked. Reproduce using `custom/scripts/record_native_transport.py`.
  This proves local transport, not AWS or model behavior; no live AWS capture exists.
- AC3: missing model setup, SDK creation failure and inference failure tests assert
  safe messages, no exception-detail leakage and no alternate backend. The native
  mate-create API regression explicitly forbids both CLI resolution and boto3 client
  creation while model is auto; it passed.
- `python -m pytest -c setup.cfg -n0 custom/tests`: 312 passed.
- `python -m pytest -n0 test/test_harness_parity.py test/test_member_dispatch_mount.py
  test/test_mate_worker.py test/test_acp_frame_replay.py test/test_x_feature_captures.py`:
  353 passed. Harness/X-capture gate against origin/main also passed.
- Clean config/KAS regression run: 248 passed.
  The initially sandbox-blocked HTTP tests were rerun with loopback enabled:
  `python -m pytest -n0 test/test_api_agents_create_template.py`: 47 passed.
- Flake-pattern gate: 64 passed. Targeted lint and Linux-platform mypy on the three
  changed custom runtime modules passed. Line-cap gate passed with an unchanged
  upstream run_turn.py warning. Repository docs lint and tracker checks passed.
  No full suite or live AWS inference run.
- AC4 remains open for installed-runtime/config adoption. Existing optional CLI
  harnesses remain source code, not a dependency of the native Bedrock path.

## Next steps

Install/rebuild the fork into the desktop runtime before updating both active
backend fields to bedrock. Keep model auto until the user configures Bedrock;
chat then returns the requested error. Restart only once that runtime is ready.
The source fallback-error change is complete; installed application adoption and
SPEC-0001 model/API coverage remain open. No commit or push performed.
