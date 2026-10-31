# SPEC-0003: Isolate custom features inside this repository

## Intent

Keep our custom work in a dedicated `custom/` directory in this same Git repository.
The user explicitly rejected a separate repository. Reduce upstream merge conflicts
by retaining only required integration bridges in upstream-owned paths.

## Acceptance criteria

- [x] SPEC-0003-AC1: Bedrock runtime and mirror implementation are an independently packaged `kirocrew_custom` namespace under `custom/`, with no duplicate runtime in core.
- [x] SPEC-0003-AC2: Feature specs, tracker, workflow documentation, tools and custom tests live under `custom/` with their recorded checkpoints preserved across the move.
- [x] SPEC-0003-AC3: The fork retains small, documented integration bridges and reports an actionable install error when the custom package is absent.
- [x] SPEC-0003-AC4: Offline tests and a packaged-runtime initialization smoke test pass without AWS credentials; CI explicitly discovers custom tests.
- [x] SPEC-0003-AC5: Installation, upstream sync, patch ownership and history inspection are documented; no separate repository, automatic commits or automatic pushes are created.

## Implementation

Move the native runtime into `custom/src/kirocrew_custom/bedrock/` and the agent
mirror implementation into `custom/src/kirocrew_custom/integration/`. Core launch,
identity, registry and model-validation declarations remain necessary integration
patches. Do not monkey-patch upstream registries or bypass security boundaries.

Move development records and tooling into `custom/specs/` and `custom/scripts/`.
Support historical records from their original committed paths and record every
plan relocation as an explicit migration event. Keep all work in the existing Git
repository. The directory is tracked, not ignored or a nested Git repository.

## Validation

Observed local checks:

- AC1: Built `kirocrew_custom-0.1.0-py3-none-any.whl`. A process launched from the
  extracted wheel initialized ACP and created a session using a minimal PATH,
  with no AWS credentials or agent CLI. The wheel contains only the custom
  namespace, not copied core source. Editable installation also passed.
- AC2: `python3 custom/scripts/spec_tracker.py check --base HEAD` passed against
  the original committed tracker. `show SPEC-0001 --at HEAD` read the real legacy
  plan and `history SPEC-0001` included its earlier Git commit. Prior event values
  are preserved; relocations append explicit `moved_from` events.
- AC3–AC4: A combined run of custom tests plus harness parity, provider mirrors,
  backend installation/cards and host-contract parity passed all 395 tests.
  The final custom-only run passed all 52 tests. These selections overlap.
  Missing-package and optional-mirror metadata behavior are covered. Tests use the
  root pytest configuration and host-isolation floor; no live AWS inference ran.
- AC4: Targeted mypy with `--platform linux` passed for 11 custom/bridge source
  files. Black, isort, flake8, harness added-line/capture gates, testpath collection
  coverage, YAML checks, custom document links and `git diff --check` passed.
- AC5: Full `bash scripts/docs-lint.sh` passed after updating the moved source
  citation. The custom CI workflow explicitly installs and tests both packages;
  remote CI has not run. Package ownership and upstream sync steps are documented.

Repository-wide line-cap check remains red on the pre-existing
`src/kiro_crew/subagent_manager/run_turn.py` (2,030 lines in the current HEAD).
There is no working-tree diff for that file. Custom source files are below the cap;
this extraction does not change unrelated upstream code. The full suite is not run.

## Next steps

Continue custom feature development under `custom/` and resume SPEC-0001 for
remaining model coverage. The extraction is verified locally and remains
uncommitted; commit or push only on explicit user instruction.
