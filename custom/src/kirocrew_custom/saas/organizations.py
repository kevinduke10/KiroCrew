"""Immutable organization trees and explicit memberships managed by company admins."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from botocore.exceptions import ClientError

from .access import AccessDenied, Identity, Membership
from .dynamo import DynamoRepository, encode_item
from .platform import PlatformDirectory
from .records import Conflict, InvalidRecord, identifier, membership_item
from .roles import Role

MAX_ORGANIZATION_DEPTH = 64


def organization_key(company_id: str, organization_id: str) -> dict[str, str]:
    return {"pk": "ORG#" + identifier(company_id), "sk": identifier(organization_id)}


class OrganizationService:
    def __init__(
        self,
        client: Any,
        table: str,
        platform: PlatformDirectory | None = None,
        *,
        admin_check: Callable[[Identity, str], None] | None = None,
    ) -> None:
        self.client = client
        self.table = table
        self.admin_check = admin_check
        self.platform = platform

    def _admin(self, identity: Identity, company: str) -> None:
        if self.admin_check is not None:
            self.admin_check(identity, company)
            return
        if self.platform is not None and not self.platform.company(company)["active"]:
            raise AccessDenied("Access denied")
        member = DynamoRepository(self.client, self.table).membership(identity, company)
        if member is None or not member.company_admin:
            raise AccessDenied("Access denied")

    def _organization_admin(self, identity: Identity, company: str, organization: str) -> bool:
        if self.platform is not None:
            self.platform.require_company(identity, company)
        repository = DynamoRepository(self.client, self.table)
        company_member = repository.membership(identity, company)
        member = repository.membership(identity, company, organization)
        return bool(
            company_member
            and not company_member.company_admin
            and member
            and member.role == Role.ORGANIZATION_ADMIN
            and not (self.platform and self.platform.is_superman(identity))
        )

    def permissions(self, identity: Identity, company: str, organization: str) -> dict[str, Any]:
        if self.platform is not None:
            self.platform.require_company(identity, company)
        repository = DynamoRepository(self.client, self.table)
        company_member = repository.membership(identity, company)
        superman = self.platform.is_superman(identity) if self.platform else False
        if company_member is None and not superman:
            raise AccessDenied("Access denied")
        scoped = repository.membership(identity, company, organization)
        company_admin = superman or bool(company_member and company_member.company_admin)
        return {
            "role": scoped.role_name if scoped else None,
            "manage_members": company_admin
            or bool(scoped and scoped.role == Role.ORGANIZATION_ADMIN),
            "manage_administrators": company_admin,
        }

    def get(self, company: str, organization: str) -> dict[str, Any]:
        key = organization_key(company, organization)
        item = DynamoRepository(self.client, self.table)._get(key)
        if item is None:
            raise AccessDenied("Access denied")
        try:
            if (
                any(item[k] != v for k, v in key.items())
                or item["type"] != "organization"
                or item["schema"] != 1
                or isinstance(item["schema"], bool)
                or item["company_id"] != company
                or item["organization_id"] != organization
                or not isinstance(item["name"], str)
                or not 1 <= len(item["name"].strip()) <= 128
            ):
                raise InvalidRecord("Invalid organization")
            if item["parent_id"] is not None:
                identifier(item["parent_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidRecord("Invalid organization") from exc
        return item

    def create(
        self, identity: Identity, company: str, organization: str, name: str, parent: str | None
    ) -> dict[str, Any]:
        self._admin(identity, company)
        key = organization_key(company, organization)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
            raise ValueError("Invalid name")
        seen = {organization}
        ancestor = parent
        while ancestor is not None:
            identifier(ancestor)
            if ancestor in seen or len(seen) >= MAX_ORGANIZATION_DEPTH:
                raise ValueError("Invalid hierarchy")
            seen.add(ancestor)
            ancestor = self.get(company, ancestor)["parent_id"]
        item = {
            **key,
            "type": "organization",
            "schema": 1,
            "company_id": company,
            "organization_id": organization,
            "parent_id": parent,
            "name": name.strip(),
            "created_by": asdict(identity),
        }
        # Parents are immutable; no API operation can reparent or delete them.
        self._admin(identity, company)
        try:
            self.client.put_item(
                TableName=self.table,
                Item=encode_item(item),
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise Conflict("Organization already exists") from exc
            raise
        return {k: item[k] for k in ("organization_id", "parent_id", "name")}

    def set_member(
        self,
        identity: Identity,
        company: str,
        organization: str,
        subject: str,
        active: bool,
        groups: list[str],
        role: Role = Role.MEMBER,
    ) -> dict[str, Any]:
        delegated = self._organization_admin(identity, company, organization)
        if not delegated:
            self._admin(identity, company)
        self.get(company, organization)
        target = Identity(identity.issuer, subject)
        if not isinstance(subject, str) or not subject or len(subject) > 128:
            raise ValueError("Invalid subject")
        if type(active) is not bool or not isinstance(groups, list) or len(groups) > 100:
            raise ValueError("Invalid membership")
        target_company_member = DynamoRepository(self.client, self.table).membership(
            target, company
        )
        if target_company_member is None:
            raise AccessDenied("Access denied")
        if delegated and target_company_member.company_admin:
            raise AccessDenied("Organization administrators cannot alter company administrators")
        if role not in {Role.MEMBER, Role.ORGANIZATION_ADMIN}:
            raise ValueError("Invalid organization role")
        current = DynamoRepository(self.client, self.table).membership(
            target, company, organization
        )
        if delegated and (
            role != Role.MEMBER or (current is not None and current.role != Role.MEMBER)
        ):
            raise AccessDenied(
                "Organization administrators cannot appoint or remove administrators"
            )
        member = Membership(
            target, company, frozenset(identifier(g) for g in groups), False, organization, role
        )
        item = membership_item(member, active=active)
        item["updated_by"] = asdict(identity)
        if delegated:
            if not self._organization_admin(identity, company, organization):
                raise AccessDenied("Access denied")
        else:
            self._admin(identity, company)
        self.client.put_item(TableName=self.table, Item=encode_item(item))
        return {"organization_id": organization, "subject": subject, "active": active}
