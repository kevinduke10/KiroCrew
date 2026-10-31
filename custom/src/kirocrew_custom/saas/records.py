"""Strict DynamoDB record format and collision-free scoped keys."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .access import Action, Grant, Identity, Membership, Resource, ResourceKey
from .roles import Role

SCHEMA_VERSION = 1
MAX_CONTENT_BYTES = 65536
MAX_GRANTS = 100
MAX_GROUPS = 100
IDENTIFIER = r"[A-Za-z0-9_-]{1,128}"


class InvalidRecord(ValueError):
    """An authoritative record violates the storage contract."""


class Conflict(RuntimeError):
    """The requested resource revision is no longer current."""


def identifier(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(IDENTIFIER, value) is None:
        raise ValueError("Invalid identifier")
    return value


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def content_json(value: Any) -> str:
    encoded = canonical(value)
    if len(encoded.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError("Content exceeds size limit")
    return encoded


def identity_record(identity: Identity) -> dict[str, str]:
    if not identity.issuer or not identity.subject:
        raise InvalidRecord("Invalid identity")
    return {"issuer": identity.issuer, "subject": identity.subject}


def parse_identity(value: Any) -> Identity:
    if not isinstance(value, dict) or set(value) != {"issuer", "subject"}:
        raise InvalidRecord("Invalid identity")
    if any(not isinstance(v, str) or not v for v in value.values()):
        raise InvalidRecord("Invalid identity")
    return Identity(**value)


def company_key(company_id: str) -> str:
    return "COMPANY#" + identifier(company_id)


def member_key(company_id: str, identity: Identity, organization_id: str = "") -> dict[str, str]:
    digest = hashlib.sha256(canonical(identity_record(identity)).encode("utf-8")).hexdigest()
    prefix = "ORG_MEMBER#" + identifier(organization_id) + "#" if organization_id else "MEMBER#"
    return {
        "pk": (
            ("ORG#" + identifier(company_id) + "#" + identifier(organization_id))
            if organization_id
            else company_key(company_id)
        ),
        "sk": prefix + digest,
    }


def resource_prefix(organization_id: str) -> str:
    return "RESOURCE#" + identifier(organization_id) + "#"


def resource_key(key: ResourceKey) -> dict[str, str]:
    return {
        "pk": company_key(key.company_id),
        "sk": resource_prefix(key.organization_id)
        + canonical([identifier(key.space_id), identifier(key.resource_id)]),
    }


def membership_item(member: Membership, *, active: bool = True) -> dict[str, Any]:
    if type(active) is not bool or type(member.administrator) is not bool:
        raise InvalidRecord("Invalid membership flags")
    groups = sorted(identifier(group) for group in member.groups)
    if len(groups) > MAX_GROUPS:
        raise InvalidRecord("Too many groups")
    return {
        **member_key(member.company_id, member.identity, member.organization_id),
        "schema": SCHEMA_VERSION,
        "type": "membership",
        "company_id": member.company_id,
        "organization_id": member.organization_id,
        "identity": identity_record(member.identity),
        "active": active,
        "administrator": member.administrator,
        "role": member.role_name,
        "groups": groups,
    }


def parse_membership(
    item: dict[str, Any], identity: Identity, company_id: str, organization_id: str = ""
) -> Membership | None:
    try:
        groups = item["groups"]
        if (
            (type(item["schema"]) is bool or item["schema"] != SCHEMA_VERSION)
            or item["type"] != "membership"
            or item["company_id"] != company_id
            or item.get("organization_id", "") != organization_id
            or parse_identity(item["identity"]) != identity
            or any(
                item[k] != v for k, v in member_key(company_id, identity, organization_id).items()
            )
            or type(item["active"]) is not bool
            or type(item["administrator"]) is not bool
            or not isinstance(groups, list)
            or len(groups) > MAX_GROUPS
        ):
            raise InvalidRecord("Invalid membership record")
        member = Membership(
            identity,
            company_id,
            frozenset(identifier(g) for g in groups),
            item["administrator"],
            organization_id,
            Role(item["role"]) if "role" in item else None,
        )
        if member.administrator != item["administrator"]:
            raise InvalidRecord("Contradictory role and administrator flag")
        return member if item["active"] else None
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRecord("Invalid membership record") from exc


def grant_record(grant: Grant) -> dict[str, Any]:
    if not isinstance(grant.action, Action) or (grant.identity is None) == (grant.group is None):
        raise InvalidRecord("Invalid grant")
    return {
        "action": grant.action.value,
        "identity": identity_record(grant.identity) if grant.identity else None,
        "group": identifier(grant.group) if grant.group is not None else None,
    }


def parse_grant(value: Any) -> Grant:
    if not isinstance(value, dict) or set(value) != {"action", "identity", "group"}:
        raise InvalidRecord("Invalid grant")
    grant = Grant(
        Action(value["action"]),
        parse_identity(value["identity"]) if value["identity"] is not None else None,
        value["group"],
    )
    grant_record(grant)
    return grant


@dataclass(frozen=True)
class Snapshot:
    resource: Resource
    revision: str


def resource_item(resource: Resource, revision: str, content: Any) -> dict[str, Any]:
    if type(resource.private) is not bool or len(resource.grants) > MAX_GRANTS:
        raise InvalidRecord("Invalid resource flags or grants")
    return {
        **resource_key(resource.key),
        "schema": SCHEMA_VERSION,
        "type": "resource",
        "company_id": resource.key.company_id,
        "organization_id": resource.key.organization_id,
        "space_id": resource.key.space_id,
        "resource_id": resource.key.resource_id,
        "kind": identifier(resource.kind),
        "private": resource.private,
        "owner": identity_record(resource.owner),
        "grants": [grant_record(grant) for grant in resource.grants],
        "revision": identifier(revision),
        "content": content_json(content),
    }


def parse_snapshot(item: dict[str, Any], key: ResourceKey) -> Snapshot:
    try:
        grants = item["grants"]
        if (
            (type(item["schema"]) is bool or item["schema"] != SCHEMA_VERSION)
            or item["type"] != "resource"
            or item["company_id"] != key.company_id
            or item["organization_id"] != key.organization_id
            or item["space_id"] != key.space_id
            or item["resource_id"] != key.resource_id
            or any(item[k] != v for k, v in resource_key(key).items())
            or type(item["private"]) is not bool
            or not isinstance(grants, list)
            or len(grants) > MAX_GRANTS
        ):
            raise InvalidRecord("Invalid resource record")
        resource = Resource(
            key,
            identifier(item["kind"]),
            item["private"],
            parse_identity(item["owner"]),
            tuple(parse_grant(grant) for grant in grants),
        )
        return Snapshot(resource, identifier(item["revision"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRecord("Invalid resource record") from exc
