# SPEC-0002: Persistent spec-driven development workflow

## Intent

The user requested a spec-driven development setup that agents can track and revisit
through history. The workflow must survive chat boundaries and expose unfinished
work and evidence. Its owning contract is
[spec-driven development](../docs/spec-driven-development.md).

## Acceptance criteria

- [x] SPEC-0002-AC1: Repository instructions require reading and updating indexed feature plans across work sessions, including acceptance criteria and the next action.
- [x] SPEC-0002-AC2: A versioned tracker gives features stable IDs, explicit status and append-only checkpoints, with Bedrock seeded honestly from existing work.
- [x] SPEC-0002-AC3: A local read-only command lists work, shows plans and checkpoints, and reads committed versions without changing the checkout.
- [x] SPEC-0002-AC4: Local validation rejects malformed records, missing plans, incomplete verified features and rewritten checkpoints relative to a Git base; focused tests pass.
- [x] SPEC-0002-AC5: Documentation indexes and a CI workflow include the tracker checks; documentation validation passes locally.

## Implementation

Custom feature plans live in `custom/specs/`; core behavior keeps its existing
module-spec hierarchy. The tracker lives in `custom/specs/work-tracker.json`;
`custom/scripts/spec_tracker.py` reads and
validates it using only Python's standard library. Agents update the human-readable
plans and structured tracker together. Recorded checkpoints supplement Git history
until a human authorizes a commit. The maintainer decision ledger is separate.

The workflow does not create commits, publish changes, or claim a live-service test
from mocked evidence. The CI workflow is configured locally and executes on GitHub
after publication; a local pass is not a claim that remote CI ran.

## Validation

Observed local verification:

- AC1–AC2: Agent and contributor instructions link the indexed workflow; the tracker
  contains stable feature IDs and the Bedrock plan explicitly preserves the open
  model-coverage requirement.
- AC3–AC4: `python3 -m pytest -c setup.cfg -n0 custom/tests/test_spec_tracker.py` passed all 19 tests.
  Tests cover append-only history, deleted IDs, stable plan paths, incomplete
  acceptance, malformed records, and historical reads without checkout mutation.
  `list`, `show`, `history`, and `check --base origin/main` also passed CLI smoke
  checks. Historical `show --at` is exercised with mocked Git output and the extraction also smoke-checks the real legacy revision.
- AC5: `bash scripts/docs-lint.sh` passed. The workflow YAML parsed locally, with
  read-only permissions and a base-ref comparison configured. Remote CI has not run.
- Black, isort, flake8 and `git diff --check` passed for this setup. The test run
  reports existing pytest/asyncio deprecation warnings; there are no test failures.

## Next steps

Use the tracker for the next feature or to resume SPEC-0001. The relocation remains uncommitted pending explicit user instruction; earlier
committed versions remain readable through the legacy paths.
