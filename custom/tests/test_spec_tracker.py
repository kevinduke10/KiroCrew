"""Feature tracker validation and historical reads isolated from the checkout."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_tracker.py"
_spec = importlib.util.spec_from_file_location("spec_tracker_under_test", _SCRIPT)
assert _spec and _spec.loader
tracker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tracker)


@pytest.fixture
def feature_tree(tmp_path):
    feature = {
        "id": "SPEC-0001",
        "title": "Example",
        "status": "in_progress",
        "spec": "custom/specs/example.md",
        "next_step": "Verify behavior",
        "history": [{"seq": 1, "date": "2026-10-29", "status": "in_progress", "note": "Started"}],
    }
    plan = tmp_path / feature["spec"]
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "# SPEC-0001: Example\n\n## Intent\n\nAn observable feature.\n\n"
        "## Acceptance criteria\n\n- [ ] SPEC-0001-AC1: Behavior is proven.\n\n"
        "## Implementation\n\nImplement behavior.\n\n## Validation\n\nNot run.\n\n"
        "## Next steps\n\nVerify behavior.\n",
        encoding="utf-8",
    )
    data = {"schema_version": 1, "features": [feature]}
    (tmp_path / tracker.TRACKER).write_text(json.dumps(data), encoding="utf-8")
    return tmp_path, data, plan


def test_in_progress_accepts_unfinished_criteria(feature_tree):
    root, data, _ = feature_tree
    tracker.validate(root, data)


@pytest.mark.parametrize("status", ["verified", "shipped"])
def test_completion_requires_checked_acceptance(feature_tree, status):
    root, data, _ = feature_tree
    data["features"][0]["status"] = status
    data["features"][0]["history"][-1]["status"] = status
    with pytest.raises(ValueError, match="unchecked acceptance"):
        tracker.validate(root, data)


def test_verified_plan_and_appended_checkpoint_preserve_old_events(feature_tree):
    root, data, plan = feature_tree
    previous = copy.deepcopy(data)
    feature = data["features"][0]
    feature["status"] = "verified"
    feature["next_step"] = "Await requested commit"
    feature["history"].append(
        {
            "seq": 2,
            "date": "2026-10-29",
            "status": "verified",
            "note": "Checks passed",
        }
    )
    plan.write_text(plan.read_text(encoding="utf-8").replace("[ ]", "[x]"), encoding="utf-8")
    tracker.validate(root, data, previous)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("event", "append-only"),
        ("delete", "cannot be deleted"),
        ("checkpoint", "require a new history event"),
        ("path", "path must remain stable"),
    ],
)
def test_base_comparison_preserves_identity_and_checkpoints(feature_tree, change, reason):
    root, data, plan = feature_tree
    previous = copy.deepcopy(data)
    feature = data["features"][0]
    if change == "event":
        feature["history"][0]["note"] = "Rewritten"
    elif change == "delete":
        data["features"] = []
    elif change == "checkpoint":
        feature["next_step"] = "Different action"
    else:
        feature["spec"] = "custom/specs/moved.md"
        plan.rename(root / feature["spec"])
    with pytest.raises(ValueError, match=reason):
        tracker.validate(root, data, previous)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("duplicate", "duplicate feature ID"),
        ("path", "Invalid plan path"),
        ("section", "missing or empty section"),
        ("criterion", "acceptance IDs"),
        ("sequence", "sequence must be consecutive"),
        ("status", "match the last history"),
    ],
)
def test_invalid_records_are_rejected(feature_tree, change, reason):
    root, data, plan = feature_tree
    feature = data["features"][0]
    if change == "duplicate":
        data["features"].append(copy.deepcopy(feature))
    elif change == "path":
        feature["spec"] = "../outside.md"
    elif change == "section":
        plan.write_text(
            plan.read_text(encoding="utf-8").replace("## Validation", "## Removed"),
            encoding="utf-8",
        )
    elif change == "criterion":
        plan.write_text(
            plan.read_text(encoding="utf-8").replace("SPEC-0001-AC1", "SPEC-0002-AC1"),
            encoding="utf-8",
        )
    elif change == "sequence":
        feature["history"][0]["seq"] = 2
    else:
        feature["status"] = "ready"
    with pytest.raises(ValueError, match=reason):
        tracker.validate(root, data)


def test_historical_show_reads_committed_content_without_mutating_files(
    feature_tree, monkeypatch, capsys
):
    root, data, plan = feature_tree
    original = plan.read_bytes()
    calls = []

    def git(_root, *args):
        calls.append(args)
        if args[0] == "rev-parse":
            return "a" * 40
        if args[0] == "ls-tree":
            return tracker.TRACKER.as_posix()
        if args[1].endswith(tracker.TRACKER.as_posix()):
            return json.dumps(data)
        return "Historical plan\n"

    monkeypatch.setattr(tracker, "git", git)
    assert tracker.main(["show", "SPEC-0001", "--at", "HEAD~1"], root=root) == 0
    assert capsys.readouterr().out == "Historical plan\n"
    assert plan.read_bytes() == original
    assert calls[0] == ("rev-parse", "--verify", "--end-of-options", "HEAD~1^{commit}")
    assert calls[-1] == ("show", "a" * 40 + ":" + data["features"][0]["spec"])


def test_unknown_feature_returns_failure(feature_tree, capsys):
    root, _, _ = feature_tree
    assert tracker.main(["show", "SPEC-9999"], root=root) == 1
    assert "Unknown feature" in capsys.readouterr().err


def test_history_includes_uncommitted_checkpoint(feature_tree, monkeypatch, capsys):
    root, _, _ = feature_tree
    monkeypatch.setattr(tracker, "git", lambda *args: "")
    assert tracker.main(["history", "SPEC-0001"], root=root) == 0
    assert "[in_progress] Started" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["unnumbered", "empty_note"])
def test_tracker_refuses_untraceable_acceptance_or_checkpoint(feature_tree, change):
    root, data, plan = feature_tree
    if change == "unnumbered":
        plan.write_text(
            plan.read_text(encoding="utf-8").replace(
                "## Acceptance criteria", "## Acceptance criteria\n\n- [ ] Unnumbered behavior"
            ),
            encoding="utf-8",
        )
    else:
        data["features"][0]["history"][0]["note"] = None
    with pytest.raises(ValueError):
        tracker.validate(root, data)


def test_explicit_relocation_preserves_checkpoint_prefix(feature_tree):
    root, data, _ = feature_tree
    previous = copy.deepcopy(data)
    old = "docs/request-for-change/plans/example.md"
    previous["features"][0]["spec"] = old
    data["features"][0]["history"].append(
        {
            "seq": 2,
            "date": "2026-10-29",
            "status": "in_progress",
            "note": "User-directed relocation",
            "moved_from": old,
        }
    )
    tracker.validate(root, data, previous)


def test_historical_read_finds_tracker_at_legacy_location(feature_tree, monkeypatch, capsys):
    root, data, _ = feature_tree
    data["features"][0]["spec"] = "docs/request-for-change/plans/example.md"
    calls = []

    def git(_root, *args):
        calls.append(args)
        if args[0] == "rev-parse":
            return "b" * 40
        if args[0] == "ls-tree":
            return tracker.LEGACY_TRACKER.as_posix()
        if args[1].endswith(tracker.LEGACY_TRACKER.as_posix()):
            return json.dumps(data)
        return "Plan before relocation\n"

    monkeypatch.setattr(tracker, "git", git)
    assert tracker.main(["show", "SPEC-0001", "--at", "HEAD"], root=root) == 0
    assert capsys.readouterr().out == "Plan before relocation\n"
    assert calls[-1][-1].endswith(":docs/request-for-change/plans/example.md")
