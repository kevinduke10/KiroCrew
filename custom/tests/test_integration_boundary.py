"""The optional custom distribution connects through narrow core bridges."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_has_no_dependency_on_the_core_namespace():
    source = ROOT / "custom/src/kirocrew_custom/bedrock"
    assert (source / "__main__.py").is_file()
    for path in source.glob("*.py"):
        assert "from kiro_crew" not in path.read_text(encoding="utf-8")
        assert "import kiro_crew" not in path.read_text(encoding="utf-8")
    assert not list((ROOT / "src/kiro_crew/bedrock_runtime").glob("*.py"))


def test_every_declared_integration_patch_still_exists():
    manifest = json.loads((ROOT / "custom/integration.json").read_text(encoding="utf-8"))
    for patch in manifest["core_patches"]:
        assert (ROOT / patch["path"]).is_file(), patch["path"]
        assert patch["purpose"].strip()


@pytest.mark.asyncio
async def test_missing_custom_package_is_reported_before_any_session_setup(monkeypatch, tmp_path):
    from kiro_crew.acp.harness.base import SpawnContext
    from kiro_crew.acp.harness.bedrock import BedrockLaunch

    monkeypatch.setattr("kiro_crew.acp.harness.bedrock.find_spec", lambda name: None)
    context = SpawnContext(
        agent="kirocrew", work_dir=tmp_path, model="vendor.test-v1:0", environ={}, home=tmp_path
    )
    with pytest.raises(RuntimeError, match=r"custom\[bedrock\]"):
        await BedrockLaunch().resolve_spawn(context)


def test_mirror_metadata_remains_readable_without_custom_package(monkeypatch):
    from kiro_crew.providers.mirrors.base import Concern, Disposition
    from kiro_crew.providers.mirrors.bedrock import BedrockMirror

    monkeypatch.setattr("kiro_crew.providers.mirrors.bedrock.find_spec", lambda name: None)
    rulings = BedrockMirror().rulings()
    assert set(rulings) == set(Concern)
    assert all(rule.disposition == Disposition.WITHHELD for rule in rulings.values())
