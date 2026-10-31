"""Authoritative platform grants and company directory; no role mutation API."""

from __future__ import annotations

import hashlib
from typing import Any

from .access import AccessDenied, Identity
from .dynamo import decode_item, encode_item
from .records import (
    InvalidRecord,
    canonical,
    identifier,
    identity_record,
    member_key,
    parse_identity,
)
from .roles import Role

PAGE_SIZE = 50


def identity_digest(identity: Identity) -> str:
    return hashlib.sha256(canonical(identity_record(identity)).encode()).hexdigest()


def platform_key(identity: Identity) -> dict[str, str]:
    return {"pk": "PLATFORM", "sk": identity_digest(identity)}


def platform_role_item(identity: Identity, *, active: bool) -> dict[str, Any]:
    """For a separately authorized AWS operator, never an HTTP request handler."""
    if type(active) is not bool:
        raise ValueError("Invalid platform grant")
    return {
        **platform_key(identity),
        "schema": 1,
        "identity": identity_record(identity),
        "role": "superman",
        "active": active,
    }


def company_record_key(company: str) -> dict[str, str]:
    return {"pk": "COMPANIES", "sk": identifier(company)}


class PlatformDirectory:
    def __init__(self, client: Any, *, roles_table: str, control_table: str, records_table: str):
        self.client = client
        self.roles_table = roles_table
        self.control_table = control_table
        self.records_table = records_table

    def get(self, table: str, key: dict[str, str]) -> dict[str, Any] | None:
        item = self.client.get_item(TableName=table, Key=encode_item(key), ConsistentRead=True).get(
            "Item"
        )
        return decode_item(item) if item else None

    def is_superman(self, identity: Identity) -> bool:
        item = self.get(self.roles_table, platform_key(identity))
        if item is None:
            return False
        if (
            type(item.get("schema")) is bool
            or item.get("schema") != 1
            or item.get("role") != "superman"
            or type(item.get("active")) is not bool
            or parse_identity(item.get("identity")) != identity
            or any(item.get(k) != v for k, v in platform_key(identity).items())
        ):
            raise InvalidRecord("Invalid platform role")
        return item["active"] is True

    def company(self, company: str) -> dict[str, Any]:
        item = self.get(self.control_table, company_record_key(company))
        if item is None:
            raise AccessDenied("Access denied")
        if (
            item.get("schema") != 1
            or type(item.get("schema")) is bool
            or item.get("company_id") != company
            or type(item.get("active")) is not bool
            or not isinstance(item.get("name"), str)
            or not item["name"]
            or any(item.get(k) != v for k, v in company_record_key(company).items())
        ):
            raise InvalidRecord("Invalid company record")
        identifier(item.get("revision"))
        return item

    def require_company(self, identity: Identity, company: str) -> None:
        record = self.company(company)
        if not record["active"] and not self.is_superman(identity):
            raise AccessDenied("Access denied")

    def membership(self, identity: Identity, company: str):
        from .records import parse_membership

        item = self.get(self.records_table, member_key(company, identity))
        return parse_membership(item, identity, company) if item else None

    def require_admin(self, identity: Identity, company: str) -> None:
        if not self.company(company)["active"]:
            raise AccessDenied("Access denied")
        if self.is_superman(identity):
            return
        member = self.membership(identity, company)
        if member is None or not member.company_admin:
            raise AccessDenied("Access denied")

    def require_organization_admin(
        self, identity: Identity, company: str, organization: str
    ) -> None:
        if not self.company(company)["active"]:
            raise AccessDenied("Access denied")
        if self.is_superman(identity):
            return
        member = self.membership(identity, company)
        if member is None:
            raise AccessDenied("Access denied")
        if member.company_admin:
            return
        from .records import parse_membership

        item = self.get(self.records_table, member_key(company, identity, organization))
        scoped = parse_membership(item, identity, company, organization) if item else None
        if scoped is None or scoped.role != Role.ORGANIZATION_ADMIN:
            raise AccessDenied("Access denied")

    def require_owner(self, identity: Identity, company: str) -> None:
        if not self.company(company)["active"]:
            raise AccessDenied("Access denied")
        if self.is_superman(identity):
            return
        member = self.membership(identity, company)
        if member is None or member.role != Role.COMPANY_OWNER:
            raise AccessDenied("Access denied")
        if parse_identity(self.company(company).get("owner")) != identity:
            raise AccessDenied("Access denied")

    def page(self, partition: str, after: str | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {
            "TableName": self.control_table,
            "ConsistentRead": True,
            "Limit": PAGE_SIZE,
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": encode_item({":pk": partition}),
        }
        if after is not None:
            request["ExclusiveStartKey"] = encode_item({"pk": partition, "sk": identifier(after)})
        response = self.client.query(**request)
        items = [decode_item(item) for item in response.get("Items", [])]
        for item in items:
            if item.get("pk") != partition:
                raise InvalidRecord("Invalid directory partition")
            identifier(item.get("sk"))
        last = (
            decode_item(response["LastEvaluatedKey"]) if response.get("LastEvaluatedKey") else None
        )
        return {"items": items, "next": identifier(last["sk"]) if last else None}
