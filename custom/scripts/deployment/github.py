"""Read-only checks of GitHub environment gates and rollback release provenance."""

import json
import urllib.request
from urllib.parse import quote


def get(path: str, token: str):
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def check_environments(repository: str, token: str, fetch=get) -> None:
    for environment in (
        "dev-plan",
        "dev-apply",
        "test-plan",
        "test-apply",
        "prod-plan",
        "prod-apply",
    ):
        root = f"repos/{repository}/environments/{quote(environment, safe='')}"
        policy = fetch(root, token)
        if not policy.get("deployment_branch_policy", {}).get("custom_branch_policies"):
            raise ValueError(f"{environment} must restrict deployments to main")
        rules = fetch(root + "/deployment-branch-policies?per_page=100", token)
        branches = rules.get("branch_policies", [])
        if (
            len(branches) != 1
            or branches[0].get("name") != "main"
            or branches[0].get("type") != "branch"
        ):
            raise ValueError(f"{environment} must allow only the main branch")
        if environment == "prod-apply":
            reviewers = [
                rule
                for rule in policy.get("protection_rules", [])
                if rule.get("type") == "required_reviewers"
            ]
            if (
                not reviewers
                or not reviewers[0].get("reviewers")
                or not reviewers[0].get("prevent_self_review")
            ):
                raise ValueError("prod-apply requires reviewers and no self-review")


def check_rollback(repository: str, token: str, run_id: str, fetch=get) -> None:
    if not run_id.isdigit():
        raise ValueError("Rollback run ID must be numeric")
    run = fetch(f"repos/{repository}/actions/runs/{run_id}", token)
    if (
        run.get("path") != ".github/workflows/custom-deploy.yml"
        or run.get("head_branch") != "main"
        or run.get("conclusion") != "success"
        or run.get("event") != "workflow_dispatch"
    ):
        raise ValueError("Rollback requires a successful main-branch deployment run")
