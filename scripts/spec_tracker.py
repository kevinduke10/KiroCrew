#!/usr/bin/env python3
"""Inspect feature plans and validate their persistent checkpoint history."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRACKER = Path("docs/request-for-change/plans/work-tracker.json")
STATES = frozenset({"draft", "ready", "in_progress", "blocked", "verified", "shipped", "cancelled"})
SECTIONS = ("Intent", "Acceptance criteria", "Implementation", "Validation", "Next steps")
FEATURE_ID = re.compile(r"SPEC-\d{4}")
CRITERION = re.compile(r"^- \[([ xX])\] (SPEC-\d{4}-AC\d+): (.+)$", re.MULTILINE)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git command failed")
    return result.stdout


def revision(root: Path, ref: str) -> str:
    return git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()


def load(root: Path, commit: str | None = None) -> dict[str, Any]:
    content = (
        git(root, "show", f"{commit}:{TRACKER.as_posix()}")
        if commit
        else (root / TRACKER).read_text(encoding="utf-8")
    )
    data = json.loads(content)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Tracker must be a schema_version 1 object")
    if not isinstance(data.get("features"), list):
        raise ValueError("Tracker features must be an array")
    return data


def plan_path(root: Path, feature: dict[str, Any]) -> Path:
    value = feature.get("spec")
    if not isinstance(value, str):
        raise ValueError("Feature spec must be a repository-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".md":
        raise ValueError(f"Invalid plan path: {value}")
    if path.parent != TRACKER.parent or path.name == "README.md":
        raise ValueError(f"Plan must live in {TRACKER.parent}")
    if not (root / path).resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Plan escapes checkout: {value}")
    return path


def validate(root: Path, data: dict[str, Any], previous: dict[str, Any] | None = None) -> None:
    ids: set[str] = set()
    paths: set[str] = set()
    for feature in data["features"]:
        if not isinstance(feature, dict):
            raise ValueError("Each feature must be an object")
        identity = feature.get("id", "")
        if not isinstance(identity, str) or not FEATURE_ID.fullmatch(identity) or identity in ids:
            raise ValueError(f"Invalid or duplicate feature ID: {identity}")
        ids.add(identity)
        for field in ("title", "next_step"):
            if not isinstance(feature.get(field), str) or not feature[field].strip():
                raise ValueError(f"{identity}: {field} is required")
        if feature.get("status") not in STATES:
            raise ValueError(f"{identity}: invalid status")
        path = plan_path(root, feature)
        if path.as_posix() in paths:
            raise ValueError(f"{identity}: plan path is already assigned")
        paths.add(path.as_posix())
        text = (root / path).read_text(encoding="utf-8")
        if not text.startswith(f"# {identity}: "):
            raise ValueError(f"{identity}: plan heading must carry its ID")
        for section in SECTIONS:
            match = re.search(rf"^## {re.escape(section)}\n(.+?)(?=^## |\Z)", text, re.M | re.S)
            if not match or not match[1].strip():
                raise ValueError(f"{identity}: missing or empty section {section}")
        acceptance = text.split("## Acceptance criteria\n", 1)[1].split("\n## ", 1)[0]
        criteria = CRITERION.findall(acceptance)
        if len(criteria) != len(re.findall(r"^- \[", acceptance, re.MULTILINE)):
            raise ValueError(f"{identity}: acceptance checkboxes require numbered IDs")
        criterion_ids = [item[1] for item in criteria]
        if (
            not criteria
            or len(set(criterion_ids)) != len(criterion_ids)
            or any(not item.startswith(identity + "-AC") for item in criterion_ids)
        ):
            raise ValueError(f"{identity}: acceptance IDs must be unique and belong to the feature")
        if feature["status"] in {"verified", "shipped"} and any(
            item[0] == " " for item in criteria
        ):
            raise ValueError(
                f"{identity}: verified or shipped work has unchecked acceptance criteria"
            )
        events = feature.get("history")
        if not isinstance(events, list) or not events:
            raise ValueError(f"{identity}: history is required")
        last_date = ""
        for sequence, event in enumerate(events, 1):
            if not isinstance(event, dict) or event.get("seq") != sequence:
                raise ValueError(f"{identity}: history sequence must be consecutive")
            when = event.get("date", "")
            if not isinstance(when, str) or date.fromisoformat(when).isoformat() != when:
                raise ValueError(f"{identity}: history needs ISO dates")
            if when < last_date:
                raise ValueError(f"{identity}: history dates must not go backwards")
            last_date = when
            if (
                event.get("status") not in STATES
                or not isinstance(event.get("note"), str)
                or not event["note"].strip()
            ):
                raise ValueError(f"{identity}: history needs status and note")
        if events[-1]["status"] != feature["status"]:
            raise ValueError(f"{identity}: status must match the last history event")
    if previous is not None:
        current = {feature["id"]: feature for feature in data["features"]}
        for old in previous["features"]:
            feature = current.get(old["id"])
            if feature is None:
                raise ValueError(f"{old['id']}: tracked features cannot be deleted")
            if feature["spec"] != old["spec"]:
                raise ValueError(f"{old['id']}: plan path must remain stable")
            if feature["history"][: len(old["history"])] != old["history"]:
                raise ValueError(f"{old['id']}: history is append-only")
            changed = any(feature[key] != old[key] for key in ("title", "status", "next_step"))
            if changed and len(feature["history"]) == len(old["history"]):
                raise ValueError(f"{old['id']}: checkpoint changes require a new history event")


def base_tracker(root: Path, ref: str) -> dict[str, Any] | None:
    base = git(root, "merge-base", "HEAD", revision(root, ref)).strip()
    paths = git(root, "ls-tree", "--name-only", base, "--", TRACKER.as_posix()).splitlines()
    return load(root, base) if TRACKER.as_posix() in paths else None


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show statuses and next actions")
    show = commands.add_parser("show", help="read a plan, optionally at a committed revision")
    show.add_argument("id")
    show.add_argument("--at")
    history = commands.add_parser("history", help="show checkpoints and Git changes")
    history.add_argument("id")
    check = commands.add_parser("check", help="validate tracker and plan consistency")
    check.add_argument("--base", help="also enforce append-only history against the merge base")
    args = parser.parse_args(argv)
    try:
        commit = revision(root, args.at) if args.command == "show" and args.at else None
        data = load(root, commit)
        if args.command == "check":
            previous = base_tracker(root, args.base) if args.base else None
            validate(root, data, previous)
            print(f"Tracker valid: {len(data['features'])} feature(s)")
        elif args.command == "list":
            for feature in data["features"]:
                print(f"{feature['id']}  {feature['status']:<12}  {feature['title']}")
                print(f"  Next: {feature['next_step']}")
        else:
            feature = next((item for item in data["features"] if item["id"] == args.id), None)
            if feature is None:
                raise ValueError(f"Unknown feature: {args.id}")
            path = plan_path(root, feature)
            if args.command == "show":
                text = (
                    git(root, "show", f"{commit}:{path.as_posix()}")
                    if commit
                    else (root / path).read_text(encoding="utf-8")
                )
                print(text, end="")
            else:
                for event in feature["history"]:
                    print(f"{event['seq']}. {event['date']} [{event['status']}] {event['note']}")
                print("\nGit changes (tracker commits may also concern other features):")
                print(
                    git(
                        root, "log", "--format=%h %as %s", "--", path.as_posix(), TRACKER.as_posix()
                    ).strip()
                    or "No committed history yet."
                )
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"spec-tracker: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
