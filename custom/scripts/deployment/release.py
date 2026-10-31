"""Build and verify portable release bytes; never execute code from release metadata."""

import hashlib
import json
import re
import zipfile
from pathlib import Path

import yaml
from botocore.exceptions import ClientError


class TemplateLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    key = tag if tag in {"Ref", "Condition"} else "Fn::" + tag
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        if tag == "GetAtt":
            value = value.split(".", 1)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {key: value}


TemplateLoader.add_multi_constructor("!", intrinsic)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(package: Path, template: Path, output: Path, commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Release requires an exact source commit")
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output / "application.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if path.is_symlink():
                raise ValueError("Release package must not contain symlinks")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                info = zipfile.ZipInfo(path.relative_to(package).as_posix(), (2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    parsed = yaml.load(template.read_text(encoding="utf-8"), Loader=TemplateLoader)
    (output / "template.json").write_text(json.dumps(parsed, sort_keys=True), encoding="utf-8")
    manifest = {
        "schema": 1,
        "commit": commit,
        "files": {
            name: digest((output / name).read_bytes())
            for name in ("application.zip", "template.json")
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def verify(directory: Path) -> tuple[dict, str]:
    raw = (directory / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if (
        set(manifest) != {"schema", "commit", "files"}
        or manifest["schema"] != 1
        or not re.fullmatch(r"[0-9a-f]{40}", manifest["commit"])
    ):
        raise ValueError("Invalid release manifest")
    if set(manifest["files"]) != {"application.zip", "template.json"}:
        raise ValueError("Unexpected release files")
    for name, expected in manifest["files"].items():
        if digest((directory / name).read_bytes()) != expected:
            raise ValueError("Release digest mismatch")
    return manifest, digest(raw)


def immutable_put(s3, bucket: str, account: str, key: str, body: bytes) -> str:
    checksum = digest(body)
    try:
        result = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ExpectedBucketOwner=account,
            IfNoneMatch="*",
            ServerSideEncryption="AES256",
            Metadata={"sha256": checksum},
        )
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
            raise
        result = s3.get_object(Bucket=bucket, Key=key, ExpectedBucketOwner=account)
        if digest(result["Body"].read()) != checksum:
            raise ValueError("An immutable release object has conflicting bytes") from error
    version = result.get("VersionId")
    if not version or version == "null":
        raise ValueError("Release bucket must have versioning enabled")
    return version


def publish(s3, config: dict, directory: Path) -> tuple[str, str]:
    manifest, release_digest = verify(directory)
    prefix = "releases/" + release_digest
    bucket, account = config["artifact_bucket"], config["account_id"]
    version = immutable_put(
        s3,
        bucket,
        account,
        prefix + "/application.zip",
        (directory / "application.zip").read_bytes(),
    )
    template = json.loads((directory / "template.json").read_text(encoding="utf-8"))
    functions = [
        resource
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::Serverless::Function"
    ]
    if len(functions) != 3:
        raise ValueError("Unexpected application function set")
    for function in functions:
        function["Properties"]["CodeUri"] = {
            "Bucket": bucket,
            "Key": prefix + "/application.zip",
            "Version": version,
        }
        function.pop("Metadata", None)
    body = json.dumps(template, sort_keys=True).encode("utf-8")
    # A content-addressed key makes the URL immutable without signed URLs or expiry.
    key = prefix + "/template-" + digest(body) + ".json"
    immutable_put(s3, bucket, account, key, body)
    immutable_put(
        s3, bucket, account, prefix + "/manifest.json", (directory / "manifest.json").read_bytes()
    )
    return f"https://{bucket}.s3.{config['region']}.amazonaws.com/{key}", release_digest
