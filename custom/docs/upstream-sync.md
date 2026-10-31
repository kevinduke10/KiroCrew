# Updating upstream with the custom layer

The fork retains upstream's implementation plus the integration declarations in
[integration.json](../integration.json). Custom feature logic lives under `custom/`.
The closed ACP backend registry still requires core declarations for Bedrock's ID,
capabilities, process marker, model syntax, tool identity and permission routing.
Those are real patches to maintain; the directory boundary cannot remove API drift.

## Sync procedure

1. Start with a reviewed, clean working tree. Commit only when the user authorizes
   it. Do not use stash, reset or clean as an update shortcut.
2. Fetch the existing upstream remote with `git fetch upstream`, when authorized
   to perform the update. Review `git log --oneline HEAD..upstream/main` and
   `git diff HEAD...upstream/main --` followed by the core paths in the manifest.
3. Merge `upstream/main` into the working branch. Resolve each integration conflict
   using the manifest's purpose and the owning system spec. Keep upstream's core
   logic and adapt the custom bridge to its current contract; do not restore a
   whole old upstream file or weaken a gate to make a test pass.
4. Reinstall both editable packages, then run the custom tests, tracker checker,
   relevant core integration tests, harness parity, and documentation validation.
   The commands below assume the checkout's development dependencies are installed.
5. Record the observed upstream revision, validation evidence and unresolved
   compatibility work in the manifest and feature tracker. Only advance the
   dependency range after verifying the new core. The workflow does not fetch,
   merge, commit or push automatically.

```bash
python3 -m pip install -e . -e './custom[bedrock]'
python3 -m pytest -c setup.cfg -n0 custom/tests
python3 -m pytest -n0 test/test_harness_parity.py test/test_provider_mirrors.py test/test_agent_sdk_backend_install.py test/test_backend_cards.py test/test_agent_host_contract_parity.py
python3 custom/scripts/spec_tracker.py check --base HEAD
HARNESS_BASE_REF=upstream/main python3 scripts/check_harness_parity.py
bash scripts/docs-lint.sh
```

## Boundaries to preserve

- Root `AGENTS.md`, contributor docs and indexes carry short pointers to custom
  ownership; the detailed workflow belongs here.
- Core model and backend declarations are explicit. Runtime selection never uses
  a monkey-patch, copied core module or an alternate permission path.
- `custom/` is included in Git and the dedicated CI job. It has its own package
  metadata, but shares this repository's commit and review history.
- A future upstream addition named `custom/` can conflict. Inspect the incoming
  tree and choose an explicit relocation with recorded history if that happens.
- Upstream in-app updates or a stock core wheel do not install the custom wheel.
  Distribute/install both together and use this fork for the required bridges.
  Source layout alone does not make a stock upstream installation support Bedrock.
