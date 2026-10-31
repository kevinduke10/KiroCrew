# Custom layer rules

This folder is our tracked customization layer inside the existing Git repository.
It is not a nested repository, a submodule, a generated overlay or an ignored folder.

- Read [README.md](README.md), [the spec workflow](docs/spec-driven-development.md),
  and the relevant plan before implementation. Run
  `python3 custom/scripts/spec_tracker.py list` from the repository root.
- Keep custom feature logic, specs, tests and developer scripts in this directory.
  Avoid copying upstream modules. Use existing extension seams where available;
  document every necessary core integration patch in [integration.json](integration.json).
- Update acceptance criteria, evidence, next steps and append-only tracker events
  before ending a work session. Preserve historical events during explicit moves.
- Core subsystem changes still require reading/updating their owning system specs.
  Keep ACP permission routing and the OS sandbox intact. Do not monkey-patch core
  registries to evade backend admission or capability declarations.
- Run custom tests from the repository root with
  `python3 -m pytest -c setup.cfg -n0 custom/tests` so root host-isolation fixtures
  apply. The custom package's own testpaths also identify this nested distribution
  to collection scanners; the dedicated CI workflow runs the explicit root command.
- Do not commit, push, reset, clean or alter remotes unless the user asks.
