"""Persistent adapter, audit ordering and revision tests without AWS access."""

from dataclasses import replace
from itertools import count

import boto3
import pytest
from botocore.stub import Stubber
from kirocrew_custom.saas.access import (
    AccessDenied,
    Action,
    AuditEvent,
    Grant,
    Membership,
    Resource,
)
from kirocrew_custom.saas.dynamo import DynamoAudit, DynamoRepository, decode_item, encode_item
from kirocrew_custom.saas.records import (
    Conflict,
    InvalidRecord,
    member_key,
    membership_item,
    parse_membership,
    parse_snapshot,
    resource_item,
    resource_key,
)
from kirocrew_custom.saas.service import ResourceService
from saas_helpers import ADMIN, KEY, NOW, OWNER, PEER, MemoryDynamo


@pytest.fixture
def persistence():
    client = MemoryDynamo()
    ids = count(1)
    service = ResourceService(
        client=client,
        records_table="records",
        audit=DynamoAudit(client, "audit"),
        clock=lambda: NOW,
        new_id=lambda: f"operation-{next(ids)}",
    )
    return client, service


def test_real_sdk_accepts_exact_consistent_membership_request():
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    item = membership_item(Membership(OWNER, KEY.company_id))
    with Stubber(client) as stub:
        stub.add_response(
            "get_item",
            {"Item": encode_item(item)},
            {
                "TableName": "records",
                "Key": encode_item(member_key(KEY.company_id, OWNER)),
                "ConsistentRead": True,
            },
        )
        assert DynamoRepository(client, "records").membership(OWNER, KEY.company_id) == Membership(
            OWNER, KEY.company_id
        )
        stub.assert_no_pending_responses()


def test_private_admin_content_is_read_only_after_audit_and_completion(persistence):
    client, service = persistence
    assert service.read(ADMIN, KEY) == {"content": {"text": "secret"}, "revision": "v1"}
    assert [kind for kind, _ in client.calls] == ["get", "get", "get", "audit", "get", "audit"]
    assert "content" not in client.calls[2][1]["ExpressionAttributeNames"].values()
    assert [item["outcome"] for item in client.audit] == ["authorized_before_access", "completed"]
    assert client.audit[0]["resource"]["company_id"] == KEY.company_id
    assert "secret" not in str(client.audit)


@pytest.mark.parametrize("stage", ["authorized_before_access", "completed"])
def test_audit_outage_cannot_return_content(persistence, stage):
    client, service = persistence
    client.fail_audit = stage
    with pytest.raises(Exception, match="sensitive upstream text"):
        service.read(ADMIN, KEY)
    if stage == "authorized_before_access":
        assert [kind for kind, _ in client.calls] == ["get", "get", "get", "audit"]


def test_content_failure_records_failed_attempt_without_content(persistence):
    client, service = persistence
    client.fail_content = True
    with pytest.raises(Exception, match="sensitive upstream text"):
        service.read(ADMIN, KEY)
    assert [item["outcome"] for item in client.audit] == ["authorized_before_access", "failed"]


def test_audit_record_cannot_be_overwritten(persistence):
    client, _ = persistence
    sink = DynamoAudit(client, "audit")
    event = AuditEvent("same-operation", ADMIN, KEY, Action.READ, NOW)
    assert sink.record_access(event)
    with pytest.raises(Exception, match="ConditionalCheckFailedException"):
        sink.record_access(event)
    assert len(client.audit) == 1


def test_read_detects_changed_acl_snapshot_before_returning_content(persistence):
    client, service = persistence

    def change_acl():
        item = client.items[tuple(resource_key(KEY).values())]
        item["private"] = False
        item["revision"] = "v2"

    client.before_content = change_acl
    with pytest.raises(Conflict):
        service.read(ADMIN, KEY)
    assert client.audit[-1]["outcome"] == "failed"


def test_edit_updates_only_content_and_revision_and_rejects_stale_client(persistence):
    client, service = persistence
    result = service.edit(OWNER, KEY, {"text": "edited"}, "v1")
    assert service.read(OWNER, KEY) == {"content": {"text": "edited"}, **result}
    with pytest.raises(Conflict):
        service.edit(OWNER, KEY, {"text": "lost update"}, "v1")
    update = next(request for kind, request in client.calls if kind == "update")
    assert set(update["ExpressionAttributeNames"].values()) == {"content", "revision"}
    assert update["ConditionExpression"] == "#revision = :expected"
    assert decode_item(update["ExpressionAttributeValues"])[":expected"] == "v1"


def test_dynamodb_conditional_conflict_is_reported(persistence):
    client, _ = persistence
    repository = DynamoRepository(client, "records")
    resource = repository.resource(KEY)
    client.items[tuple(resource_key(KEY).values())]["revision"] = "v2"
    with pytest.raises(Conflict):
        repository.edit(resource, {}, expected_revision="v1", next_revision="v3")


@pytest.mark.parametrize("identity", [PEER, replace(OWNER, issuer="other-issuer")])
def test_private_coworker_and_unregistered_issuer_cannot_access(persistence, identity):
    client, service = persistence
    with pytest.raises(AccessDenied):
        service.read(identity, KEY)
    assert not client.audit


def test_cross_company_admin_is_not_a_global_role(persistence):
    _, service = persistence
    with pytest.raises(AccessDenied):
        service.read(ADMIN, replace(KEY, company_id="company-b"))


def test_persisted_revocation_applies_to_next_request(persistence):
    client, service = persistence
    assert service.read(OWNER, KEY)
    client.put(membership_item(Membership(OWNER, KEY.company_id), active=False))
    with pytest.raises(AccessDenied):
        service.read(OWNER, KEY)


@pytest.mark.parametrize(
    "field,value",
    [
        ("active", "true"),
        ("administrator", "false"),
        ("groups", "hr"),
        ("company_id", "other-company"),
        ("identity", {"issuer": "evil", "subject": "owner"}),
        ("schema", 999),
        ("schema", True),
    ],
)
def test_malformed_membership_never_grants_access(field, value):
    item = membership_item(Membership(OWNER, KEY.company_id))
    item[field] = value
    with pytest.raises(InvalidRecord):
        parse_membership(item, OWNER, KEY.company_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("private", "false"),
        ("grants", [{"action": "read", "group": None, "identity": None}]),
        ("space_id", "other-space"),
        ("revision", ""),
        ("owner", {}),
        ("schema", 99),
        ("schema", True),
    ],
)
def test_malformed_resource_never_reaches_content(field, value):
    item = resource_item(Resource(KEY, "conversation", True, OWNER), "v1", {})
    item[field] = value
    with pytest.raises(InvalidRecord):
        parse_snapshot(item, KEY)


def test_scoped_keys_reject_delimiter_path_injection_and_do_not_collide():
    assert resource_key(KEY) != resource_key(replace(KEY, resource_id="other"))
    assert member_key(KEY.company_id, OWNER) != member_key(
        KEY.company_id, replace(OWNER, issuer="other")
    )
    for key in [replace(KEY, space_id="a#b"), replace(KEY, company_id="../other")]:
        with pytest.raises(ValueError):
            resource_key(key)


def test_admin_does_not_inherit_edit_permissions(persistence):
    _, service = persistence
    with pytest.raises(AccessDenied):
        service.edit(ADMIN, KEY, {}, "v1")


def test_shared_resource_uses_current_group_grants(persistence):
    client, service = persistence
    client.put(
        membership_item(
            Membership(PEER, KEY.company_id, frozenset({"hr"}), organization_id=KEY.organization_id)
        )
    )
    client.put(
        resource_item(
            Resource(KEY, "conversation", False, OWNER, (Grant(Action.READ, group="hr"),)), "v1", {}
        )
    )
    assert service.read(PEER, KEY)["content"] == {}
    client.put(
        membership_item(Membership(PEER, KEY.company_id, organization_id=KEY.organization_id))
    )
    with pytest.raises(AccessDenied):
        service.read(PEER, KEY)


def test_sdk_resource_projection_and_audit_put_are_valid():
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    resource = Resource(KEY, "conversation", True, OWNER)
    with Stubber(client) as stub:
        item = resource_item(resource, "v1", {})
        item.pop("content")
        stub.add_response("get_item", {"Item": encode_item(item)})
        assert DynamoRepository(client, "records").resource(KEY) == resource
        stub.add_response("put_item", {})
        assert DynamoAudit(client, "audit").record_access(
            AuditEvent("operation-1", ADMIN, KEY, Action.READ, NOW)
        )
        stub.assert_no_pending_responses()
