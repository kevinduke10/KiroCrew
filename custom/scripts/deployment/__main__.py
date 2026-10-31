"""CLI for custom deployment workflows. Cloud operations require an explicit command."""

import argparse
import json
import os
from pathlib import Path

import boto3
from botocore.config import Config

from . import github, release, runner
from .config import ENVIRONMENTS, load_config, settings


def output(values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            for key, value in values.items():
                if "\n" in str(value) or "\r" in str(value):
                    raise ValueError("Invalid workflow output")
                stream.write(f"{key}={value}\n")
    else:
        print(json.dumps(values))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["config", "preflight", "build", "verify", "plan", "apply", "drift", "bootstrap"],
    )
    parser.add_argument("--environment", choices=ENVIRONMENTS)
    parser.add_argument("--config", type=Path, default=Path("custom/deploy/environments.json"))
    parser.add_argument("--release", type=Path, default=Path("release"))
    parser.add_argument("--package", type=Path, default=Path("package"))
    args = parser.parse_args()
    if args.command == "build":
        release.build(
            args.package,
            Path("custom/deploy/pooled-api.yaml"),
            args.release,
            os.environ["GITHUB_SHA"],
        )
        return
    if args.command == "verify":
        release.verify(args.release)
        return
    config = load_config(args.config)
    if config["repository"] != os.environ.get("GITHUB_REPOSITORY", config["repository"]):
        raise ValueError("Workflow repository does not match deployment configuration")
    if args.command == "preflight":
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise ValueError("Only main may deploy")
        github.check_environments(config["repository"], os.environ["GH_TOKEN"])
        if os.environ.get("ROLLBACK_RUN_ID"):
            github.check_rollback(
                config["repository"], os.environ["GH_TOKEN"], os.environ["ROLLBACK_RUN_ID"]
            )
        return
    if not args.environment:
        raise ValueError("Select an environment")
    entry = settings(config, args.environment)
    if args.command == "bootstrap":
        destination = Path("custom/deploy/.local/bootstrap") / args.environment
        destination.mkdir(parents=True, exist_ok=True)
        source = Path("custom/deploy/terraform/bootstrap")
        for name in ("main.tf", "versions.tf", ".terraform.lock.hcl"):
            (destination / name).write_bytes((source / name).read_bytes())
        values = {
            "environment": args.environment,
            "repository": config["repository"],
            "account_id": entry["account_id"],
            "region": entry["region"],
            "hosted_zone_id": entry["hosted_zone_id"],
            "domain_name": entry["web_origin"].removeprefix("https://"),
        }
        (destination / "environment.auto.tfvars.json").write_text(
            json.dumps(values), encoding="utf-8"
        )
        backend = {
            "bucket": entry["state_bucket"],
            "key": "bootstrap/terraform.tfstate",
            "region": entry["region"],
            "encrypt": True,
            "use_lockfile": True,
            "allowed_account_ids": [entry["account_id"]],
        }
        (destination / "backend.hcl").write_text(
            "\n".join(f"{key} = {json.dumps(value)}" for key, value in backend.items()),
            encoding="utf-8",
        )
        print(f"Prepared isolated bootstrap directory: {destination}")
        return
    if args.command == "config":
        output(
            {
                key: entry[key]
                for key in ["account_id", "region", "plan_role", "apply_role", "artifact_bucket"]
            }
        )
        return
    session = boto3.Session(region_name=entry["region"])
    # All clients use bounded retries and timeouts, including drift checks.
    original_client = session.client
    session.client = lambda service: original_client(
        service,
        config=Config(
            connect_timeout=5,
            read_timeout=20,
            retries={"mode": "standard", "total_max_attempts": 3},
        ),
    )
    if args.command == "plan":
        values = runner.plan(
            session,
            config,
            args.environment,
            args.release,
            os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"],
            os.environ["GITHUB_SHA"],
        )
        output(values)
        with open(
            os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8"
        ) as stream:
            stream.write(
                f"### {args.environment} deployment plan\n\nRelease: `{values['release_digest']}`\n\nPrivate plan/template: `s3://{entry['artifact_bucket']}/{values['plan_key']}`\n\nReview using operator SSO access before approving apply. Plan expires after 24 hours.\n"
            )
    elif args.command == "apply":
        runner.apply(
            session,
            config,
            args.environment,
            os.environ["PLAN_KEY"],
            os.environ["PLAN_VERSION"],
            os.environ["PLAN_DIGEST"],
            os.environ["GITHUB_SHA"],
        )
    else:
        runner.drift(session, config, args.environment)


if __name__ == "__main__":
    main()
