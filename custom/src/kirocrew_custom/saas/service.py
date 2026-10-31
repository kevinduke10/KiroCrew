"""Compose current authorization, scoped persistence and privileged audit."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Callable

from .access import Action, AuditEvent, Identity, Resource, ResourceKey, perform
from .dynamo import DynamoAudit, DynamoRepository
from .platform import PlatformDirectory


class _ReadAudit:
    """Request-local confirmation retained for the matching completion event."""

    def __init__(self, sink: DynamoAudit) -> None:
        self.sink = sink
        self.event: AuditEvent | None = None

    def record_access(self, event: AuditEvent) -> str:
        receipt = self.sink.record_access(event)
        if receipt:
            self.event = event
        return receipt

    def finish(self, outcome: str, occurred_at: datetime) -> None:
        if self.event is not None:
            receipt = self.sink.record_outcome(
                replace(self.event, outcome=outcome, occurred_at=occurred_at)
            )
            if not receipt:
                raise RuntimeError("Audit persistence unavailable")


class ResourceService:
    def __init__(
        self,
        *,
        client: Any,
        records_table: str,
        audit: DynamoAudit,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
        platform: PlatformDirectory | None = None,
    ) -> None:
        self.client = client
        self.records_table = records_table
        self.audit = audit
        self.clock = clock
        self.new_id = new_id
        self.platform = platform

    def read(self, identity: Identity, key: ResourceKey) -> dict[str, Any]:
        if self.platform is not None:
            self.platform.require_company(identity, key.company_id)
        repository = DynamoRepository(self.client, self.records_table)
        audit = _ReadAudit(self.audit)

        def read(resource: Resource) -> dict[str, Any]:
            try:
                result = repository.read(resource)
            except Exception:
                audit.finish("failed", self.clock())
                raise
            audit.finish("completed", self.clock())
            return result

        return perform(
            identity=identity,
            key=key,
            action=Action.READ,
            repository=repository,
            audit=audit,
            operation_id=self.new_id(),
            clock=self.clock,
            operation=read,
            platform_role=self.platform.is_superman if self.platform else None,
        )

    def edit(
        self, identity: Identity, key: ResourceKey, content: Any, expected_revision: str
    ) -> dict[str, str]:
        if self.platform is not None:
            self.platform.require_company(identity, key.company_id)
        repository = DynamoRepository(self.client, self.records_table)
        audit = _ReadAudit(self.audit)

        def edit(resource: Resource) -> dict[str, str]:
            try:
                result = repository.edit(
                    resource,
                    content,
                    expected_revision=expected_revision,
                    next_revision=self.new_id(),
                )
            except Exception:
                audit.finish("failed", self.clock())
                raise
            audit.finish("completed", self.clock())
            return result

        return perform(
            identity=identity,
            key=key,
            action=Action.EDIT,
            repository=repository,
            audit=audit,
            operation_id=self.new_id(),
            clock=self.clock,
            operation=edit,
            platform_role=self.platform.is_superman if self.platform else None,
        )

    def manage_organization(
        self,
        identity: Identity,
        company: str,
        organization: str,
        operation,
        *,
        membership: bool = False,
        details: tuple[tuple[str, str], ...] = (),
    ):
        if self.platform is None:
            return operation()
        if membership:
            self.platform.require_organization_admin(identity, company, organization)
        else:
            self.platform.require_admin(identity, company)
        audit = _ReadAudit(self.audit)
        event = AuditEvent(
            self.new_id(),
            identity,
            ResourceKey(company, "administration", organization, organization),
            Action.MANAGE,
            self.clock(),
            details=details,
        )
        if not audit.record_access(event):
            raise RuntimeError("Audit unavailable")
        try:
            result = operation()
        except Exception:
            audit.finish("failed", self.clock())
            raise
        audit.finish("completed", self.clock())
        return result
