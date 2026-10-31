"""Offline release integrity, account isolation and approval enforcement."""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from botocore.exceptions import ClientError

from custom.scripts.deployment import github, release, runner
from custom.scripts.deployment.config import load_config, settings, verify_account

ROOT = Path(__file__).resolve().parents[1]


def configuration(tmp_path):
    data = json.loads((ROOT / "deploy/environments.json").read_text())
    for index, item in enumerate(data["environments"].values()):
        item["account_id"] = str(123456789012 + index)
        item["hosted_zone_id"] = "ZEXAMPLE"
        item["embedding_model_arn"] = (
            "arn:aws:bedrock:us-east-1::foundation-model/example.embedding-v1"
        )
    path = tmp_path / "environments.json"
    path.write_text(json.dumps(data))
    return path, data


def test_placeholder_configuration_cannot_deploy():
    with pytest.raises(ValueError, match="account ID"):
        load_config(ROOT / "deploy/environments.json")


@pytest.mark.parametrize("field", ["account_id", "web_origin", "cognito_domain_prefix"])
def test_environments_cannot_share_identity_or_account(tmp_path, field):
    path, data = configuration(tmp_path)
    data["environments"]["test"][field] = data["environments"]["dev"][field]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_config(path)


def test_configuration_and_sts_enforce_explicit_account(tmp_path):
    path, _ = configuration(tmp_path)
    config = load_config(path)
    selected = settings(config, "prod")
    assert selected["state_bucket"] == "kc-prod-123456789014-state"
    assert selected["apply_role"].endswith("kc-prod-123456789014-apply")
    sts = Mock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    with pytest.raises(ValueError, match="credentials"):
        verify_account(sts, selected["account_id"])


def built_release(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "app.py").write_text("print('application')\n")
    destination = tmp_path / "release"
    release.build(package, ROOT / "deploy/pooled-api.yaml", destination, "a" * 40)
    return package, destination


def test_release_bytes_are_stable_and_tampering_is_rejected(tmp_path):
    package, destination = built_release(tmp_path)
    first = (destination / "application.zip").read_bytes()
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert all((item.external_attr >> 16) & 0o777 == 0o644 for item in archive.infolist())
    release.build(package, ROOT / "deploy/pooled-api.yaml", destination, "a" * 40)
    assert (destination / "application.zip").read_bytes() == first
    release.verify(destination)
    (destination / "application.zip").write_bytes(first + b"changed")
    with pytest.raises(ValueError, match="digest"):
        release.verify(destination)


def test_immutable_upload_retries_only_matching_bytes():
    s3 = Mock()
    s3.put_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
    s3.get_object.return_value = {"Body": io.BytesIO(b"same"), "VersionId": "version1"}
    assert release.immutable_put(s3, "bucket", "123456789012", "key", b"same") == "version1"
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    s3.get_object.return_value = {"Body": io.BytesIO(b"different"), "VersionId": "version1"}
    with pytest.raises(ValueError, match="conflicting"):
        release.immutable_put(s3, "bucket", "123456789012", "key", b"same")


def test_publish_preserves_lambda_bytes_and_pins_object_version(tmp_path):
    _, destination = built_release(tmp_path)
    path, _ = configuration(tmp_path)
    config = settings(load_config(path), "dev")
    s3 = Mock()
    s3.put_object.return_value = {"VersionId": "version-1"}
    url, _ = release.publish(s3, config, destination)
    requests = [call.kwargs for call in s3.put_object.call_args_list]
    assert requests[0]["Body"] == (destination / "application.zip").read_bytes()
    packaged = json.loads(requests[1]["Body"])
    functions = [
        r for r in packaged["Resources"].values() if r["Type"] == "AWS::Serverless::Function"
    ]
    assert all(r["Properties"]["CodeUri"]["Version"] == "version-1" for r in functions)
    assert url.startswith(
        "https://kc-dev-123456789012-artifacts.s3.us-east-1.amazonaws.com/releases/"
    )


def protected_environment():
    return {
        "deployment_branch_policy": {"custom_branch_policies": True},
        "can_admins_bypass": False,
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "User"}],
            }
        ],
    }


def test_production_approval_rules_are_mandatory():
    policy = protected_environment()

    def fetch(path, token):
        return (
            {"branch_policies": [{"name": "main", "type": "branch"}]}
            if "deployment-branch-policies" in path
            else policy
        )

    github.check_environments("owner/repo", "token", fetch)
    policy["protection_rules"][0]["prevent_self_review"] = False
    with pytest.raises(ValueError, match="prod-apply"):
        github.check_environments("owner/repo", "token", fetch)
    policy["protection_rules"][0]["prevent_self_review"] = True
    policy["protection_rules"] = []
    with pytest.raises(ValueError, match="prod-apply"):
        github.check_environments("owner/repo", "token", fetch)


def test_rollback_rejects_untrusted_or_failed_builds():
    run = {
        "path": ".github/workflows/custom-deploy.yml",
        "head_branch": "main",
        "conclusion": "success",
        "event": "workflow_dispatch",
    }
    github.check_rollback("owner/repo", "token", "123", lambda *_: run)
    for field, value in [
        ("conclusion", "failure"),
        ("head_branch", "feature"),
        ("event", "pull_request"),
        ("path", "another.yml"),
    ]:
        changed = {**run, field: value}
        with pytest.raises(ValueError, match="successful"):
            github.check_rollback("owner/repo", "token", "123", lambda *_: changed)


def test_plan_account_configuration_revision_and_expiry_are_bound(tmp_path):
    path, _ = configuration(tmp_path)
    config = settings(load_config(path), "prod")
    receipt = {
        "environment": "prod",
        "account_id": config["account_id"],
        "commit": "a" * 40,
        "configuration": release.digest(json.dumps(config, sort_keys=True).encode()),
        "created": 100,
    }
    runner.receipt_matches(receipt, "prod", config, "a" * 40, 101)
    for changed, commit, now in [
        (config, "b" * 40, 101),
        ({**config, "account_id": "123456789012"}, "a" * 40, 101),
        ({**config, "region": "us-west-2"}, "a" * 40, 101),
        (config, "a" * 40, 100000),
    ]:
        with pytest.raises(ValueError):
            runner.receipt_matches(receipt, "prod", changed, commit, now)


def test_drift_never_applies_and_fails_on_nested_resource_changes():
    client = Mock()
    client.detect_stack_drift.return_value = {"StackDriftDetectionId": "request"}
    client.describe_stack_drift_detection_status.return_value = {
        "DetectionStatus": "DETECTION_COMPLETE",
        "StackDriftStatus": "DRIFTED",
    }
    with pytest.raises(ValueError, match="drift"):
        runner.stack_drift(
            client, "stack", sleep=Mock(side_effect=AssertionError("No delay expected"))
        )
    client.update_stack.assert_not_called()


def test_workflows_use_serial_promotion_private_plans_and_pinned_actions():
    workflows = ROOT.parent / ".github/workflows"
    deploy = yaml.safe_load((workflows / "custom-deploy.yml").read_text())
    jobs = deploy["jobs"]
    assert jobs["dev"]["needs"] == "release"
    assert jobs["test"]["needs"] == "dev"
    assert jobs["prod"]["needs"] == "test"
    child = yaml.safe_load((workflows / "custom-deploy-environment.yml").read_text())
    assert child["jobs"]["apply"]["needs"] == "plan"
    assert child["jobs"]["apply"]["environment"].endswith("-apply")
    assert "upload-artifact" not in (workflows / "custom-deploy-environment.yml").read_text()
    import re

    for path in workflows.glob("custom-*.yml"):
        for action in re.findall(r"uses: ([^\s]+)", path.read_text()):
            assert action.startswith("./") or re.search(r"@[0-9a-f]{40}$", action)


def test_managed_rag_and_runtime_boundary_are_in_shared_template():
    template = yaml.load(
        (ROOT / "deploy/pooled-api.yaml").read_text(), Loader=release.TemplateLoader
    )
    resources = template["Resources"]
    for name in [
        "PooledApiFunction",
        "ControlApiFunction",
        "KnowledgeApiFunction",
        "KnowledgeServiceRole",
    ]:
        assert "PermissionsBoundary" in resources[name]["Properties"]
    assert (
        resources["ManagedKnowledgeBase"]["Properties"]["StorageConfiguration"]["Type"]
        == "S3_VECTORS"
    )
    assert resources["KnowledgeVectorIndex"]["DeletionPolicy"] == "Retain"
    assert resources["ManagedDataSource"]["Properties"]["DataDeletionPolicy"] == "RETAIN"


def test_backend_uses_correct_root_account_and_locking(tmp_path, monkeypatch):
    assert runner.ROOT == ROOT
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    directory = tmp_path / "deploy/terraform/environments/dev"
    directory.mkdir(parents=True)
    terraform = Mock()
    monkeypatch.setattr(runner, "terraform", terraform)
    path, _ = configuration(tmp_path)
    config = settings(load_config(path), "dev")
    assert runner.initialize("dev", config) == directory
    backend = (directory / "backend.hcl").read_text()
    assert 'allowed_account_ids = ["123456789012"]' in backend
    assert "use_lockfile = true" in backend
    assert 'key = "application/terraform.tfstate"' in backend
    assert "-lockfile=readonly" in terraform.call_args.args


def test_plan_and_apply_use_identical_private_saved_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    _, release_dir = built_release(tmp_path)
    path, _ = configuration(tmp_path)
    config = load_config(path)
    directory = tmp_path / "terraform"
    directory.mkdir()
    monkeypatch.setattr(runner, "initialize", lambda *_: directory)
    monkeypatch.setattr(runner, "wall_time", lambda: 1000)
    commands = []

    def terraform(environment, command, *args, **kwargs):
        commands.append((command, args))
        if command == "plan":
            (directory / "application.tfplan").write_bytes(b"saved immutable plan")
        if command == "show":
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "resource_changes": [
                            {
                                "address": "module.application.aws_cloudformation_stack.application",
                                "change": {"actions": ["update"]},
                            }
                        ]
                    }
                )
            )
        if command == "output":
            return SimpleNamespace(stdout=json.dumps({"ApiUrl": "https://test.invalid"}))
        if command == "apply":
            assert args[-1] == "application.tfplan"
            assert (directory / "application.tfplan").read_bytes() == b"saved immutable plan"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner, "terraform", terraform)
    monkeypatch.setattr(runner, "smoke", Mock())
    objects = {}
    s3, sts, cfn = Mock(), Mock(), Mock()

    def put(**request):
        objects[request["Key"]] = request["Body"]
        return {"VersionId": "version"}

    s3.put_object.side_effect = put
    s3.get_object.side_effect = lambda **request: {"Body": io.BytesIO(objects[request["Key"]])}
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    session = Mock()
    session.client.side_effect = {"sts": sts, "s3": s3, "cloudformation": cfn}.__getitem__
    plan = runner.plan(session, config, "dev", release_dir, "run-1", "a" * 40)
    runner.apply(
        session,
        config,
        "dev",
        plan["plan_key"],
        plan["plan_version"],
        plan["plan_digest"],
        "a" * 40,
    )
    assert [command for command, _ in commands].count("plan") == 1
    assert [command for command, _ in commands].count("apply") == 1
    cfn.update_termination_protection.assert_called_once_with(
        StackName="kirocrew-dev", EnableTerminationProtection=True
    )
    assert "deployments/current.json" in objects
    assert (
        json.loads(objects["deployments/current.json"])["parameters"]["ManageKnowledge"] == "true"
    )
    objects[plan["plan_key"]] += b"tampered"
    with pytest.raises(ValueError, match="digest"):
        runner.apply(
            session,
            config,
            "dev",
            plan["plan_key"],
            plan["plan_version"],
            plan["plan_digest"],
            "a" * 40,
        )
    assert [command for command, _ in commands].count("apply") == 1
