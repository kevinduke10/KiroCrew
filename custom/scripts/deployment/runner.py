"""Account-bound plan/apply, private saved plans and bounded drift/health checks."""

import io
import json
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path
from time import time as wall_time

from .config import settings, verify_account
from .release import digest, immutable_put, publish

ROOT = Path(__file__).resolve().parents[2]


def terraform(environment, *args, capture=False, accepted=(0,)):
    result = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'deploy/terraform/environments' / environment}", *args],
        text=True,
        encoding="utf-8",
        capture_output=capture,
        timeout=3300,
        check=False,
    )
    if result.returncode not in accepted:
        raise RuntimeError(f"Terraform {args[0]} failed ({result.returncode})")
    return result


def initialize(environment, config):
    root = ROOT / "deploy/terraform/environments" / environment
    backend = {
        "bucket": config["state_bucket"],
        "key": "application/terraform.tfstate",
        "region": config["region"],
        "encrypt": True,
        "use_lockfile": True,
        "allowed_account_ids": [config["account_id"]],
    }
    (root / "backend.hcl").write_text(
        "\n".join(f"{key} = {json.dumps(value)}" for key, value in backend.items()),
        encoding="utf-8",
    )
    terraform(
        environment,
        "init",
        "-input=false",
        "-reconfigure",
        "-lockfile=readonly",
        "-backend-config=backend.hcl",
    )
    return root


def variables(config, template_url, release_digest):
    return {
        "account_id": config["account_id"],
        "region": config["region"],
        "template_url": template_url,
        "release_digest": release_digest,
        "hosted_zone_id": config["hosted_zone_id"],
        "domain_name": config["web_origin"].removeprefix("https://"),
        "cloudformation_role_arn": config["cloudformation_role"],
        "parameters": {
            "CallbackUrl": config["callback_url"],
            "LogoutUrl": config["logout_url"],
            "WebOrigin": config["web_origin"],
            "CognitoDomainPrefix": config["cognito_domain_prefix"],
            "RuntimeBoundaryArn": config["boundary"],
            "ManageKnowledge": "true",
            "EmbeddingModelArn": config["embedding_model_arn"],
            "EmbeddingDimensions": str(config["embedding_dimensions"]),
            "KnowledgeBaseId": "",
            "KnowledgeDataSourceId": "",
        },
    }


def receipt_matches(receipt, environment, config, commit, now):
    if (
        receipt["environment"] != environment
        or receipt["account_id"] != config["account_id"]
        or receipt["commit"] != commit
    ):
        raise ValueError("Saved plan belongs to a different account, environment or commit")
    if receipt["configuration"] != digest(json.dumps(config, sort_keys=True).encode()):
        raise ValueError("Configuration changed after planning")
    if not 0 <= now - receipt["created"] <= 86400:
        raise ValueError("Plan expired; create a new plan and obtain new approval")


def plan(session, all_config, environment, release_dir, run_id, commit):
    config = settings(all_config, environment)
    verify_account(session.client("sts"), config["account_id"])
    s3 = session.client("s3")
    url, release_digest = publish(s3, config, release_dir)
    root = initialize(environment, config)
    values = variables(config, url, release_digest)
    (root / "release.auto.tfvars.json").write_text(json.dumps(values), encoding="utf-8")
    terraform(environment, "plan", "-input=false", "-lock-timeout=5m", "-out=application.tfplan")
    description = json.loads(
        terraform(environment, "show", "-json", "application.tfplan", capture=True).stdout
    )
    changes = [
        (item["address"], item["change"]["actions"])
        for item in description.get("resource_changes", [])
    ]
    if any("delete" in actions for _, actions in changes):
        raise ValueError(
            "Deployment refuses resource deletion/replacement; use an explicit migration"
        )
    files = {
        "application.tfplan": (root / "application.tfplan").read_bytes(),
        "release.auto.tfvars.json": (root / "release.auto.tfvars.json").read_bytes(),
        "template.json": (release_dir / "template.json").read_bytes(),
    }
    receipt = {
        "environment": environment,
        "account_id": config["account_id"],
        "commit": commit,
        "configuration": digest(json.dumps(config, sort_keys=True).encode()),
        "created": wall_time(),
        "release_digest": release_digest,
        "files": {name: digest(body) for name, body in files.items()},
    }
    files["receipt.json"] = json.dumps(receipt, sort_keys=True).encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, body in files.items():
            zipped.writestr(name, body)
    body = archive.getvalue()
    key = f"releases/{release_digest}/plans/{run_id}/{environment}-{digest(body)}.zip"
    version = immutable_put(s3, config["artifact_bucket"], config["account_id"], key, body)
    print(
        json.dumps(
            {"environment": environment, "changes": changes, "release_digest": release_digest}
        )
    )
    return {
        "plan_key": key,
        "plan_version": version,
        "plan_digest": digest(body),
        "release_digest": release_digest,
    }


def unpack_plan(body, expected_digest, environment, config, commit):
    if digest(body) != expected_digest:
        raise ValueError("Saved plan digest mismatch")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        if (
            set(archive.namelist())
            != {"receipt.json", "application.tfplan", "release.auto.tfvars.json", "template.json"}
            or len(archive.namelist()) != 4
        ):
            raise ValueError("Unexpected saved plan files")
        files = {name: archive.read(name) for name in archive.namelist()}
    receipt = json.loads(files.pop("receipt.json"))
    receipt_matches(receipt, environment, config, commit, wall_time())
    if receipt["files"] != {name: digest(data) for name, data in files.items()}:
        raise ValueError("Saved plan contents changed")
    return files, receipt


def smoke(outputs, config):
    base = config["web_origin"]
    # The CloudFormation output is trusted, but keep credentials out of this public probe.
    with urllib.request.urlopen(base + "/auth/config", timeout=20) as response:
        login = json.load(response)
    if (
        login["issuer"] != outputs["CognitoIssuer"]
        or login["client_id"] != outputs["CognitoClientId"]
        or not login["knowledge_enabled"]
    ):
        raise ValueError("Deployed identity/knowledge configuration does not match the stack")
    with urllib.request.urlopen(base + "/", timeout=20) as response:
        if response.status != 200:
            raise ValueError("Web shell health check failed")


def apply(session, all_config, environment, key, version, expected_digest, commit):
    config = settings(all_config, environment)
    verify_account(session.client("sts"), config["account_id"])
    if not key.startswith("releases/") or "/plans/" not in key:
        raise ValueError("Invalid plan object key")
    s3 = session.client("s3")
    body = s3.get_object(
        Bucket=config["artifact_bucket"],
        Key=key,
        VersionId=version,
        ExpectedBucketOwner=config["account_id"],
    )["Body"].read()
    files, receipt = unpack_plan(body, expected_digest, environment, config, commit)
    root = initialize(environment, config)
    for name in ("application.tfplan", "release.auto.tfvars.json"):
        (root / name).write_bytes(files[name])
    terraform(environment, "apply", "-input=false", "-lock-timeout=5m", "application.tfplan")
    session.client("cloudformation").update_termination_protection(
        StackName=f"kirocrew-{environment}", EnableTerminationProtection=True
    )
    outputs = json.loads(
        terraform(environment, "output", "-json", "stack_outputs", capture=True).stdout
    )
    smoke(outputs, config)
    s3.put_object(
        Bucket=config["artifact_bucket"],
        Key="deployments/current.json",
        ExpectedBucketOwner=config["account_id"],
        Body=files["release.auto.tfvars.json"],
        ServerSideEncryption="AES256",
    )
    return receipt


def stack_drift(client, stack, sleep=time.sleep):
    request = client.detect_stack_drift(StackName=stack)["StackDriftDetectionId"]
    for _ in range(60):
        result = client.describe_stack_drift_detection_status(StackDriftDetectionId=request)
        status = result["DetectionStatus"]
        if status == "DETECTION_COMPLETE":
            if result["StackDriftStatus"] != "IN_SYNC":
                raise ValueError("CloudFormation reports nested resource drift")
            return
        if status == "DETECTION_FAILED":
            raise ValueError("CloudFormation drift detection failed")
        sleep(5)
    raise TimeoutError("CloudFormation drift detection did not finish")


def drift(session, all_config, environment):
    config = settings(all_config, environment)
    verify_account(session.client("sts"), config["account_id"])
    root = initialize(environment, config)
    values = json.loads(
        session.client("s3")
        .get_object(
            Bucket=config["artifact_bucket"],
            Key="deployments/current.json",
            ExpectedBucketOwner=config["account_id"],
        )["Body"]
        .read()
    )
    expected = variables(config, values["template_url"], values["release_digest"])
    if values != expected or not values["template_url"].startswith(
        f"https://{config['artifact_bucket']}.s3.{config['region']}.amazonaws.com/releases/"
    ):
        raise ValueError("Deployed configuration differs from Git; review before deploying")
    (root / "release.auto.tfvars.json").write_text(json.dumps(values), encoding="utf-8")
    result = terraform(
        environment,
        "plan",
        "-input=false",
        "-lock-timeout=5m",
        "-detailed-exitcode",
        accepted=(0, 2),
    )
    stack_drift(session.client("cloudformation"), f"kirocrew-{environment}")
    if result.returncode == 2:
        raise ValueError("Terraform reports drift; no changes applied")
