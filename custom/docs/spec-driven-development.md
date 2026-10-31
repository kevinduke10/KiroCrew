# Spec-driven development

Feature work is tracked in [the work tracker](../specs/work-tracker.json)
and linked [implementation plans](../specs/README.md).
This workflow applies to human and agent contributors. It supplements the existing
module specs: those remain the authority for current product behavior.

## Start and resume

1. Run `python3 custom/scripts/spec_tracker.py list` from the checkout. Read the relevant
   plan and `show SPEC-NNNN`; use `history SPEC-NNNN` to recover earlier work.
2. Before implementation, allocate the next unused `SPEC-NNNN` ID in the tracker.
   Add a plan under `custom/specs/`, dated `YYYY-MM-DD-<slug>.md`,
   and link it in that directory's README. This workflow authorizes these feature
   plan files; the general rule against unrelated new Markdown files still applies.
3. Write the user's intent, numbered acceptance criteria, scope, approach, owning
   system specs, test strategy and next step before changing product code. Record
   assumptions and open scope explicitly. A large or contested design also uses
   the existing RFC process; a routine feature can link module specs directly.
4. Read the owning specs and search recorded decisions. Implementation references
   acceptance IDs in its validation evidence; user intent must not silently narrow
   to match what was convenient to implement.
5. At each meaningful checkpoint and before ending a chat, update the plan's
   checkboxes, validation and next step. Update tracker status/next_step and append
   a history event. Another chat must be able to continue from these files alone.
6. Run `python3 custom/scripts/spec_tracker.py check` and the appropriate code checks.
   Update the owning system specs alongside implementation. Mention the feature ID
   in commit bodies and PR descriptions when commits or PRs are authorized.

## Status and evidence

| Status | Meaning |
|---|---|
| `draft` | Requirements or approach still need definition. |
| `ready` | Acceptance criteria and approach are concrete enough to implement. |
| `in_progress` | Implementation or required validation remains. |
| `blocked` | A named external dependency prevents the next step. |
| `verified` | Every acceptance criterion is checked and evidence is recorded. |
| `shipped` | Verified work has a recorded merge/release reference in history. |
| `cancelled` | Work was intentionally stopped; preserve its reason and history. |

Passing unit tests does not establish live-service access, a merge or a release.
Uncommitted code is identified as such. An unchecked acceptance criterion keeps
work out of `verified` and `shipped`. A deferred part of the user's request stays
open unless the user explicitly changes scope; record that change as a new event.
Do not invent user approval or place a private chat in the maintainer decision ledger.

Each tracker entry has an immutable `id` and normally stable `spec` path, a current `title`,
`status`, `next_step`, and an append-only `history` array. Events carry consecutive
`seq` values, an ISO date, status, and a note describing the checkpoint, scope
change, validation or blocker. The last event's status equals current status.
Correct an event by appending a correction; do not rewrite it. IDs are never reused
and entries are retained after completion or cancellation.

## Plan template

Copy this structure into an indexed plan, replacing every placeholder. Acceptance
IDs remain stable even when their wording needs a documented scope amendment.
Code/test paths in validation are repository-relative and identify what proves
that criterion; include the exact commands, outcomes, limits and outstanding gaps.

```markdown
# SPEC-NNNN: Feature title

## Intent

User request and intended outcome. Owning specs: relative Markdown links.

## Acceptance criteria

- [ ] SPEC-NNNN-AC1: Observable behavior and how to verify it.

## Implementation

Scope, exclusions, approach, affected modules, and ordered tasks.

## Validation

Map each acceptance ID to tests or other concrete evidence. Distinguish planned
checks, observed passes, failures, and checks not run.

## Next steps

The next concrete action, blockers, and any open question about scope.
```

## Reviewing history

```bash
python3 custom/scripts/spec_tracker.py list
python3 custom/scripts/spec_tracker.py show SPEC-0001
python3 custom/scripts/spec_tracker.py history SPEC-0001
python3 custom/scripts/spec_tracker.py show SPEC-0001 --at HEAD
python3 custom/scripts/spec_tracker.py check --base origin/main
```

`history` shows recorded checkpoints and Git changes to the plan and tracker.
`show --at <commit-or-ref>` reads the plan at that revision without changing the
working tree. Uncommitted files are visible in ordinary `show` but cannot be read
from a Git revision. Git history begins when a human authorizes a commit; the
tracker records checkpoints in the meantime. No command commits, pushes, restores
or modifies files. To inspect a historical diff, use `git log --follow -- <plan>`
and `git show <commit> -- <plan>`. Ask explicitly before making a restore.

The checker validates IDs, local plan paths, required sections, acceptance IDs,
status/evidence coherence and event sequencing. With `--base`, it compares against
the merge base, refusing deleted IDs, unrecorded plan moves or rewritten history events.
A user-authorized relocation appends an event with `moved_from` equal to the old
plan path; previous events remain byte-equivalent as JSON values. Historical reads
also look up the legacy tracker location and old plan paths.
These are structural checks; the checker does not execute tests or judge whether
prose evidence proves a product requirement. The CI contract is in [CI and reviews](../../docs/ci/ci-and-reviews.md).
