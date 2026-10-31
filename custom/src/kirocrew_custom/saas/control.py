"""Audited company lifecycle and invitation operations for the control Lambda."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable

from botocore.exceptions import ClientError

from .access import AccessDenied, Action, AuditEvent, Identity, Membership, ResourceKey
from .dynamo import DynamoAudit, encode_item
from .platform import PlatformDirectory, company_record_key, identity_digest
from .records import (
    Conflict,
    canonical,
    identifier,
    identity_record,
    membership_item,
    parse_identity,
)
from .roles import Role


class ControlService:
    def __init__(
        self,
        *,
        directory: PlatformDirectory,
        audit: DynamoAudit,
        cognito: Any,
        pool_id: str,
        issuer: str,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
    ):
        self.directory, self.audit, self.cognito = directory, audit, cognito
        self.pool_id, self.issuer, self.clock, self.new_id = pool_id, issuer, clock, new_id

    def _audited(
        self,
        actor: Identity,
        company: str,
        target: str,
        action: Action,
        operation,
        *,
        details: tuple[tuple[str, str], ...] = (),
    ):
        event = AuditEvent(
            self.new_id(),
            actor,
            ResourceKey(company, "administration", target),
            action,
            self.clock(),
            details=details,
        )
        if not self.audit.record_access(event):
            raise RuntimeError("Audit unavailable")
        try:
            result = operation()
        except Exception:
            if not self.audit.record_outcome(
                replace(event, outcome="failed", occurred_at=self.clock())
            ):
                raise RuntimeError("Audit unavailable")
            raise
        if not self.audit.record_outcome(
            replace(event, outcome="completed", occurred_at=self.clock())
        ):
            raise RuntimeError("Audit unavailable")
        return result

    def _superman(self, actor: Identity) -> None:
        if not self.directory.is_superman(actor):
            raise AccessDenied("Access denied")

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("company_id", "name", "active", "revision")}

    def me(self, actor: Identity, after: str | None = None) -> dict[str, Any]:
        superman = self.directory.is_superman(actor)

        def listing():
            partition = "COMPANIES" if superman else "USER#" + identity_digest(actor)
            page = self.directory.page(partition, after)
            companies = []
            for pointer in page["items"]:
                company_id = pointer["sk"]
                if not superman and self.directory.membership(actor, company_id) is None:
                    continue
                company = self.directory.company(company_id)
                if not superman and not company["active"]:
                    continue
                member = self.directory.membership(actor, company_id) if not superman else None
                companies.append(
                    {
                        **self._public(company),
                        "administrator": superman or bool(member and member.company_admin),
                        "role": (
                            "superman" if superman else (member.role_name if member else "member")
                        ),
                    }
                )
            return {"superman": superman, "companies": companies, "next": page["next"]}

        return (
            self._audited(actor, "platform", "companies", Action.READ, listing)
            if superman
            else listing()
        )

    def _put(
        self, table: str, item: dict[str, Any], *, condition: str = "attribute_not_exists(pk)"
    ):
        return {
            "Put": {"TableName": table, "Item": encode_item(item), "ConditionExpression": condition}
        }

    def _transaction(self, writes: list[dict[str, Any]]) -> None:
        try:
            self.directory.client.transact_write_items(TransactItems=writes)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                raise Conflict("Concurrent change; reload and retry") from exc
            raise

    def create_company(self, actor: Identity, name: str, request_id: str) -> dict[str, Any]:
        self._superman(actor)
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Invalid company name")
        identifier(request_id)
        company_id = hashlib.sha256(
            canonical([identity_record(actor), request_id]).encode()
        ).hexdigest()[:32]
        fingerprint = hashlib.sha256(canonical({"name": name.strip()}).encode()).hexdigest()

        def create():
            previous = self.directory.get(
                self.directory.control_table, company_record_key(company_id)
            )
            if previous:
                if (
                    previous.get("creation_fingerprint") != fingerprint
                    or parse_identity(previous.get("creator")) != actor
                ):
                    raise Conflict("Idempotency key reused with different parameters")
                return self._public(self.directory.company(company_id))
            item = {
                **company_record_key(company_id),
                "schema": 1,
                "company_id": company_id,
                "name": name.strip(),
                "active": True,
                "revision": self.new_id(),
                "creator": identity_record(actor),
                "owner": identity_record(actor),
                "creation_fingerprint": fingerprint,
            }
            member = membership_item(
                Membership(actor, company_id, frozenset({"members"}), role=Role.COMPANY_OWNER)
            )
            pointer = {"pk": "USER#" + identity_digest(actor), "sk": company_id}
            root = {
                "pk": "ORG#" + company_id,
                "sk": "root",
                "type": "organization",
                "created_by": identity_record(actor),
                "schema": 1,
                "company_id": company_id,
                "organization_id": "root",
                "parent_id": None,
                "name": name.strip(),
            }
            self._transaction(
                [
                    self._put(self.directory.control_table, item),
                    self._put(self.directory.records_table, member),
                    self._put(self.directory.control_table, pointer),
                    self._put(self.directory.records_table, root),
                    self._put(
                        self.directory.records_table,
                        membership_item(
                            Membership(actor, company_id, frozenset({"members"}), False, "root")
                        ),
                    ),
                ]
            )
            return self._public(item)

        return self._audited(actor, company_id, "company", Action.MANAGE, create)

    def update_company(
        self,
        actor: Identity,
        company: str,
        name: str,
        active: bool,
        revision: str,
        request_id: str | None = None,
    ):
        superman = self.directory.is_superman(actor)
        if not superman:
            self.directory.require_owner(actor, company)
        identifier(revision)
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 120
            or type(active) is not bool
        ):
            raise ValueError("Invalid company settings")

        receipt_key = None
        fingerprint = hashlib.sha256(
            canonical([name.strip(), active, revision]).encode()
        ).hexdigest()
        if request_id is not None:
            identifier(request_id)
            digest = hashlib.sha256(
                canonical([identity_record(actor), request_id]).encode()
            ).hexdigest()
            receipt_key = {"pk": "COMPANY#" + identifier(company), "sk": "UPDATE#" + digest}

        def update():
            if receipt_key is not None:
                receipt = self.directory.get(self.directory.control_table, receipt_key)
                if receipt is not None:
                    if receipt.get("fingerprint") != fingerprint:
                        raise Conflict("Idempotency key reused with different parameters")
                    return receipt["result"]
            previous = self.directory.company(company)
            if not superman and previous["active"] != active:
                raise AccessDenied("Only platform administration can suspend a company")
            if previous["revision"] != revision:
                raise Conflict("Resource changed")
            item = {**previous, "name": name.strip(), "active": active, "revision": self.new_id()}
            writes = [
                {
                    "Put": {
                        "TableName": self.directory.control_table,
                        "Item": encode_item(item),
                        "ConditionExpression": "#revision = :expected",
                        "ExpressionAttributeNames": {"#revision": "revision"},
                        "ExpressionAttributeValues": encode_item({":expected": revision}),
                    }
                }
            ]
            if receipt_key is not None:
                writes.append(
                    self._put(
                        self.directory.control_table,
                        {**receipt_key, "fingerprint": fingerprint, "result": self._public(item)},
                    )
                )
            self._transaction(writes)
            return self._public(item)

        return self._audited(actor, company, "company", Action.MANAGE, update)

    def invite(
        self, actor: Identity, company: str, email: str, administrator: bool | Role, request_id: str
    ):
        self.directory.require_admin(actor, company)
        if type(administrator) is bool:
            role = Role.COMPANY_ADMIN if administrator else Role.MEMBER
        elif isinstance(administrator, Role):
            role = administrator
        else:
            raise ValueError("Invalid invitation role")
        if role not in {Role.COMPANY_ADMIN, Role.MEMBER}:
            raise ValueError("Invite a member before assigning ownership")
        if role == Role.COMPANY_ADMIN:
            self.directory.require_owner(actor, company)
        administrator = role == Role.COMPANY_ADMIN
        if (
            not isinstance(email, str)
            or len(email) > 254
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
            or type(administrator) is not bool
        ):
            raise ValueError("Invalid invitation")
        email = email.strip().lower()
        identifier(request_id)
        digest = hashlib.sha256(
            canonical([identity_record(actor), request_id]).encode()
        ).hexdigest()
        receipt_key = {"pk": "COMPANY#" + company, "sk": "INVITATION#" + digest}
        fingerprint = hashlib.sha256(canonical([email, administrator]).encode()).hexdigest()

        def invite():
            previous = self.directory.get(self.directory.control_table, receipt_key)
            if previous:
                if previous.get("fingerprint") != fingerprint:
                    raise Conflict("Idempotency key reused with different parameters")
                return {"subject": previous["subject"], "status": "invited"}
            try:
                user = self.cognito.admin_create_user(
                    UserPoolId=self.pool_id,
                    Username=email,
                    UserAttributes=[{"Name": "email", "Value": email}],
                    DesiredDeliveryMediums=["EMAIL"],
                )["User"]
                attributes = user.get("Attributes", [])
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "UsernameExistsException":
                    raise
                attributes = self.cognito.admin_get_user(
                    UserPoolId=self.pool_id, Username=email
                ).get("UserAttributes", [])
            subjects = [a.get("Value") for a in attributes if a.get("Name") == "sub"]
            if len(subjects) != 1 or not isinstance(subjects[0], str):
                raise RuntimeError("Identity service unavailable")
            identity = Identity(self.issuer, identifier(subjects[0]))
            member = membership_item(
                Membership(identity, company, frozenset({"members"}), administrator)
            )
            pointer = {"pk": "USER#" + identity_digest(identity), "sk": company}
            receipt = {**receipt_key, "fingerprint": fingerprint, "subject": identity.subject}
            self._transaction(
                [
                    {
                        "ConditionCheck": {
                            "TableName": self.directory.control_table,
                            "Key": encode_item(company_record_key(company)),
                            "ConditionExpression": "#active = :yes",
                            "ExpressionAttributeNames": {"#active": "active"},
                            "ExpressionAttributeValues": encode_item({":yes": True}),
                        }
                    },
                    self._put(self.directory.records_table, member),
                    self._put(
                        self.directory.records_table,
                        membership_item(
                            Membership(identity, company, frozenset({"members"}), False, "root")
                        ),
                    ),
                    self._put(self.directory.control_table, pointer),
                    self._put(self.directory.control_table, receipt),
                ]
            )
            return {"subject": identity.subject, "status": "invited"}

        return self._audited(actor, company, "invitation-" + digest, Action.MANAGE, invite)

    def set_member_role(self, actor, company, subject, role, active, revision):
        from .company_roles import set_company_role

        return set_company_role(self, actor, company, subject, role, active, revision)
