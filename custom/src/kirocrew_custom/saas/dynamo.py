"""DynamoDB adapters using exact keys, consistent reads and conditional writes."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from .access import AuditEvent, Identity, Membership, Resource, ResourceKey
from .records import (
    Conflict,
    InvalidRecord,
    Snapshot,
    company_key,
    content_json,
    identifier,
    member_key,
    parse_membership,
    parse_snapshot,
    resource_key,
)

METADATA_FIELDS = (
    "pk",
    "sk",
    "schema",
    "type",
    "company_id",
    "organization_id",
    "space_id",
    "resource_id",
    "kind",
    "private",
    "owner",
    "grants",
    "revision",
)


def encode_item(item: dict[str, Any]) -> dict[str, Any]:
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in item.items()}


def decode_item(item: dict[str, Any]) -> dict[str, Any]:
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(value) for key, value in item.items()}


class DynamoRepository:
    """Create per operation: the metadata snapshot never crosses request boundaries."""

    def __init__(self, client: Any, table: str) -> None:
        self.client = client
        self.table = table
        self.snapshot: Snapshot | None = None

    def _get(self, key: dict[str, str], *, metadata_only: bool = False) -> dict[str, Any] | None:
        request: dict[str, Any] = {
            "TableName": self.table,
            "Key": encode_item(key),
            "ConsistentRead": True,
        }
        if metadata_only:
            names = {f"#f{i}": field for i, field in enumerate(METADATA_FIELDS)}
            request["ProjectionExpression"] = ",".join(names)
            request["ExpressionAttributeNames"] = names
        response = self.client.get_item(**request)
        item = response.get("Item")
        return decode_item(item) if item else None

    def membership(
        self, identity: Identity, company_id: str, organization_id: str = ""
    ) -> Membership | None:
        item = self._get(member_key(company_id, identity, organization_id))
        return parse_membership(item, identity, company_id, organization_id) if item else None

    def resource(self, key: ResourceKey) -> Resource | None:
        item = self._get(resource_key(key), metadata_only=True)
        self.snapshot = parse_snapshot(item, key) if item else None
        return self.snapshot.resource if self.snapshot else None

    def _expected(self, resource: Resource) -> Snapshot:
        if self.snapshot is None or self.snapshot.resource != resource:
            raise Conflict("Resource changed; retry the request")
        return self.snapshot

    def read(self, resource: Resource) -> dict[str, Any]:
        expected = self._expected(resource)
        item = self._get(resource_key(resource.key))
        if item is None or parse_snapshot(item, resource.key) != expected:
            raise Conflict("Resource changed; retry the request")
        encoded = item.get("content")
        if not isinstance(encoded, str):
            raise InvalidRecord("Invalid resource content")
        try:
            content = json.loads(encoded)
            content_json(content)
        except (ValueError, TypeError, RecursionError) as exc:
            raise InvalidRecord("Invalid resource content") from exc
        return {"content": content, "revision": expected.revision}

    def edit(
        self, resource: Resource, content: Any, *, expected_revision: str, next_revision: str
    ) -> dict[str, str]:
        expected = self._expected(resource)
        if expected.revision != expected_revision:
            raise Conflict("Resource changed; reload before editing")
        encoded = content_json(content)
        identifier(next_revision)
        if next_revision == expected_revision:
            raise ValueError("A new resource revision is required")
        try:
            self.client.update_item(
                TableName=self.table,
                Key=encode_item(resource_key(resource.key)),
                UpdateExpression="SET #content = :content, #revision = :next",
                ConditionExpression="#revision = :expected",
                ExpressionAttributeNames={"#content": "content", "#revision": "revision"},
                ExpressionAttributeValues=encode_item(
                    {
                        ":content": encoded,
                        ":next": next_revision,
                        ":expected": expected_revision,
                    }
                ),
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise Conflict("Resource changed; reload before editing") from exc
            raise
        return {"revision": next_revision}


class DynamoAudit:
    """Acknowledge only completed conditional PutItem calls to a separate table."""

    def __init__(self, client: Any, table: str) -> None:
        self.client = client
        self.table = table

    def _append(self, event: AuditEvent) -> str:
        identifier(event.operation_id)
        if event.occurred_at.utcoffset() is None:
            raise ValueError("Audit timestamps must be timezone aware")
        if event.outcome not in {"authorized_before_access", "completed", "failed"}:
            raise ValueError("Invalid audit outcome")
        if any(
            key not in {"role", "previous_role", "active", "subject"}
            or not isinstance(value, str)
            or len(value) > 128
            for key, value in event.details
        ):
            raise ValueError("Invalid audit details")
        receipt = event.operation_id + "#" + event.outcome
        self.client.put_item(
            TableName=self.table,
            Item=encode_item(
                {
                    "pk": company_key(event.resource.company_id),
                    "sk": receipt,
                    "schema": 1,
                    "operation_id": event.operation_id,
                    "identity": asdict(event.identity),
                    "resource": asdict(event.resource),
                    "action": event.action.value,
                    "occurred_at": event.occurred_at.isoformat(),
                    "outcome": event.outcome,
                    "details": dict(event.details),
                }
            ),
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
        return receipt

    def record_access(self, event: AuditEvent) -> str:
        if event.outcome != "authorized_before_access":
            raise ValueError("Invalid access audit event")
        return self._append(event)

    def record_outcome(self, event: AuditEvent) -> str:
        if event.outcome not in {"completed", "failed"}:
            raise ValueError("Invalid completion audit event")
        return self._append(event)
