"""Validate explicit deployment inputs before requesting AWS credentials."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ENVIRONMENTS = ("dev", "test", "prod")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if set(config) != {"repository", "environments"} or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config["repository"]
    ):
        raise ValueError("Configure the exact GitHub repository")
    environments = config["environments"]
    if set(environments) != set(ENVIRONMENTS):
        raise ValueError("Configure exactly dev, test and prod")
    for name, entry in environments.items():
        if set(entry) != {
            "account_id",
            "region",
            "hosted_zone_id",
            "web_origin",
            "callback_url",
            "logout_url",
            "cognito_domain_prefix",
            "embedding_model_arn",
            "embedding_dimensions",
        }:
            raise ValueError(f"Unexpected configuration fields in {name}")
        account = entry["account_id"]
        if (
            not isinstance(account, str)
            or not re.fullmatch(r"[0-9]{12}", account)
            or len(set(account)) == 1
        ):
            raise ValueError(f"Supply a real expected account ID for {name}")
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]", entry["region"]):
            raise ValueError("Supply a commercial AWS Region")
        if not isinstance(entry["hosted_zone_id"], str) or not re.fullmatch(
            r"Z[A-Z0-9]+", entry["hosted_zone_id"]
        ):
            raise ValueError("Supply the environment's Route53 public hosted zone ID")
        origin = urlsplit(entry["web_origin"])
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
            or origin.port
        ):
            raise ValueError("Supply an HTTPS web origin without a path")
        for key in ("callback_url", "logout_url"):
            url = urlsplit(entry[key])
            if (
                (url.scheme, url.netloc) != (origin.scheme, origin.netloc)
                or url.fragment
                or url.username
            ):
                raise ValueError("Login URLs must belong to this environment's web origin")
        if (
            not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entry["cognito_domain_prefix"])
            or len(entry["cognito_domain_prefix"]) > 63
        ):
            raise ValueError("Supply a unique Cognito domain prefix")
        if not isinstance(entry["embedding_model_arn"], str) or not re.fullmatch(
            rf"arn:aws:bedrock:{re.escape(entry['region'])}::foundation-model/[A-Za-z0-9.:-]+",
            entry["embedding_model_arn"],
        ):
            raise ValueError(
                "Supply an entitled embedding foundation-model ARN in the target Region"
            )
        if (
            type(entry["embedding_dimensions"]) is not int
            or not 1 <= entry["embedding_dimensions"] <= 4096
        ):
            raise ValueError("Supply supported embedding dimensions")
    for key in ("account_id", "web_origin", "cognito_domain_prefix"):
        if len({entry[key] for entry in environments.values()}) != 3:
            raise ValueError(f"Environments must have distinct {key} values")
    return config


def settings(config: dict, environment: str) -> dict:
    entry = config["environments"][environment]
    name = f"kc-{environment}-{entry['account_id']}"
    return {
        **entry,
        "state_bucket": name + "-state",
        "artifact_bucket": name + "-artifacts",
        "plan_role": f"arn:aws:iam::{entry['account_id']}:role/{name}-plan",
        "apply_role": f"arn:aws:iam::{entry['account_id']}:role/{name}-apply",
        "cloudformation_role": f"arn:aws:iam::{entry['account_id']}:role/{name}-cloudformation",
        "boundary": f"arn:aws:iam::{entry['account_id']}:policy/{name}-runtime-boundary",
    }


def verify_account(sts, expected: str) -> None:
    if sts.get_caller_identity()["Account"] != expected:
        raise ValueError("AWS credentials do not match the selected environment")
