"""Authoritative company/space checks for trusted service adapters (SPEC-0006)."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol, TypeVar

from .roles import COMPANY_ADMIN_ROLES, Role, normalized_role


class AccessDenied(PermissionError):
    """The requested operation is unavailable to this identity."""


class Action(str, Enum):
    READ = "read"
    RUN = "run"
    EDIT = "edit"
    MANAGE = "manage"


@dataclass(frozen=True)
class Identity:
    """Issuer and subject must come from verified authentication, never request JSON."""

    issuer: str
    subject: str


@dataclass(frozen=True)
class ResourceKey:
    company_id: str
    space_id: str
    resource_id: str
    organization_id: str = ""


@dataclass(frozen=True)
class Membership:
    identity: Identity
    company_id: str
    groups: frozenset[str] = frozenset()
    administrator: bool = False
    organization_id: str = ""
    role: Role | None = None

    def __post_init__(self):
        role = normalized_role(self.role, self.administrator, self.organization_id)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "administrator", role in COMPANY_ADMIN_ROLES)

    @property
    def role_name(self) -> str:
        return normalized_role(self.role, self.administrator, self.organization_id).value

    @property
    def company_admin(self) -> bool:
        return self.role in COMPANY_ADMIN_ROLES and not self.organization_id


@dataclass(frozen=True)
class Grant:
    action: Action
    identity: Identity | None = None
    group: str | None = None

    def matches(self, membership: Membership, action: Action) -> bool:
        if self.action != action:
            return False
        if self.identity is not None and self.group is None:
            return self.identity == membership.identity
        if self.group and self.identity is None:
            return self.group in membership.groups
        return False


@dataclass(frozen=True)
class Resource:
    key: ResourceKey
    kind: str
    private: bool
    owner: Identity
    grants: tuple[Grant, ...] = ()


@dataclass(frozen=True)
class AuditEvent:
    operation_id: str
    identity: Identity
    resource: ResourceKey
    action: Action
    occurred_at: datetime
    outcome: str = "authorized_before_access"
    details: tuple[tuple[str, str], ...] = ()


class Repository(Protocol):
    def membership(
        self, identity: Identity, company_id: str, organization_id: str = ""
    ) -> Membership | None:
        """Read current authoritative membership, or None after revocation."""
        ...

    def resource(self, key: ResourceKey) -> Resource | None:
        """Resolve exact company/space/resource scope, including effective grants."""
        ...


class AuditSink(Protocol):
    def record_access(self, event: AuditEvent) -> str:
        """Return a nonempty receipt after durable append, otherwise raise."""
        ...


T = TypeVar("T")


def perform(
    *,
    identity: Identity,
    key: ResourceKey,
    action: Action,
    repository: Repository,
    audit: AuditSink,
    operation_id: str,
    clock: Callable[[], datetime],
    operation: Callable[[Resource], T],
    platform_role: Callable[[Identity], bool] | None = None,
) -> T:
    """Authorize, audit privileged reads, then call a trusted scoped operation.

    Callers must route every content operation through this boundary. The repository
    supplies effective space grants; an agent-definition grant cannot authorize a
    conversation. No authorization result or membership is cached between calls.
    """
    if not all(
        (
            identity.issuer,
            identity.subject,
            key.company_id,
            key.space_id,
            key.resource_id,
            key.organization_id,
        )
    ):
        raise AccessDenied("Access denied")
    if not isinstance(action, Action):
        raise AccessDenied("Access denied")
    superman = platform_role(identity) is True if platform_role is not None else False
    membership = repository.membership(identity, key.company_id)
    if not superman and (
        membership is None
        or membership.identity != identity
        or membership.company_id != key.company_id
        or membership.organization_id
    ):
        raise AccessDenied("Access denied")
    organization_member = repository.membership(identity, key.company_id, key.organization_id)
    if not superman and (
        organization_member is None
        or organization_member.identity != identity
        or organization_member.company_id != key.company_id
        or organization_member.organization_id != key.organization_id
    ):
        raise AccessDenied("Access denied")
    resource = repository.resource(key)
    if resource is None or resource.key != key:
        raise AccessDenied("Access denied")
    if action == Action.RUN and resource.kind != "agent_definition":
        raise AccessDenied("Access denied")
    if superman:
        if action not in (Action.READ, Action.EDIT):
            raise AccessDenied("Access denied")
        occurred_at = clock()
        if not operation_id or occurred_at.utcoffset() is None:
            raise AccessDenied("Privileged access requires an operation ID and aware clock")
        event = AuditEvent(
            operation_id, identity, resource.key, action, occurred_at.astimezone(timezone.utc)
        )
        if not audit.record_access(event):
            raise AccessDenied("Privileged access requires a persisted audit receipt")
    elif resource.private:
        owner_access = resource.owner == identity and action in (Action.READ, Action.EDIT)
        if not owner_access:
            if (membership is None or not membership.company_admin) or action != Action.READ:
                raise AccessDenied("Access denied")
            occurred_at = clock()
            if not operation_id or occurred_at.utcoffset() is None:
                raise AccessDenied("Privileged access requires an operation ID and aware clock")
            event = AuditEvent(
                operation_id, identity, resource.key, action, occurred_at.astimezone(timezone.utc)
            )
            if not audit.record_access(event):
                raise AccessDenied("Privileged access requires a persisted audit receipt")
    elif not any(
        organization_member is not None and grant.matches(organization_member, action)
        for grant in resource.grants
    ):
        raise AccessDenied("Access denied")
    return operation(resource)
