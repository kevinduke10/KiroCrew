"""Audited, revision-checked company membership and ownership changes."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .access import AccessDenied, Action, Identity
from .dynamo import encode_item
from .records import (
    Conflict,
    identifier,
    identity_record,
    member_key,
    membership_item,
    parse_identity,
    parse_membership,
)
from .roles import COMPANY_ROLES, Role


def set_company_role(
    control: Any,
    actor: Identity,
    company: str,
    subject: str,
    role: Role,
    active: bool,
    revision: str,
) -> dict[str, Any]:
    directory = control.directory
    directory.require_admin(actor, company)
    identifier(subject)
    identifier(revision)
    if not isinstance(role, Role) or role not in COMPANY_ROLES or type(active) is not bool:
        raise ValueError("Invalid company role")
    target = Identity(actor.issuer, subject)
    old = directory.get(directory.records_table, member_key(company, target))
    if old is None:
        raise AccessDenied("Access denied")
    parse_membership(old, target, company)
    previous = parse_membership({**old, "active": True}, target, company)
    assert previous is not None
    actor_member = directory.membership(actor, company)
    privileged = directory.is_superman(actor) or (
        actor_member is not None and actor_member.role == Role.COMPANY_OWNER
    )
    if privileged:
        directory.require_owner(actor, company)
    elif previous.role != Role.MEMBER or role != Role.MEMBER:
        raise AccessDenied("Access denied")
    if previous.role == Role.COMPANY_OWNER and (role != Role.COMPANY_OWNER or not active):
        raise AccessDenied("Transfer ownership before removing an owner")
    if role == Role.COMPANY_OWNER and not active:
        raise AccessDenied("Owner membership must be active")

    def update():
        company_record = directory.company(company)
        if company_record["revision"] != revision:
            raise Conflict("Resource changed")
        item = membership_item(replace(previous, role=role), active=active)
        company_update = {**company_record, "revision": control.new_id()}
        writes = [control._put(directory.records_table, item, condition="attribute_exists(pk)")]
        if role == Role.COMPANY_OWNER:
            if old.get("active") is not True:
                raise AccessDenied("An existing active member must receive ownership")
            old_owner = (
                parse_identity(company_record["owner"]) if "owner" in company_record else None
            )
            if old_owner is not None and old_owner != target:
                owner = directory.membership(old_owner, company)
                if owner is None or owner.role != Role.COMPANY_OWNER:
                    raise AccessDenied("Invalid ownership state")
                writes.append(
                    control._put(
                        directory.records_table,
                        membership_item(replace(owner, role=Role.COMPANY_ADMIN)),
                        condition="attribute_exists(pk)",
                    )
                )
            company_update["owner"] = identity_record(target)
        writes.append(
            {
                "Put": {
                    "TableName": directory.control_table,
                    "Item": encode_item(company_update),
                    "ConditionExpression": "#revision = :expected",
                    "ExpressionAttributeNames": {"#revision": "revision"},
                    "ExpressionAttributeValues": encode_item({":expected": revision}),
                }
            }
        )
        control._transaction(writes)
        return {
            "subject": subject,
            "role": role.value,
            "active": active,
            "revision": company_update["revision"],
        }

    return control._audited(
        actor,
        company,
        "member-" + subject,
        Action.MANAGE,
        update,
        details=(
            ("subject", subject),
            ("role", role.value),
            ("previous_role", previous.role_name),
            ("active", str(active).lower()),
        ),
    )
