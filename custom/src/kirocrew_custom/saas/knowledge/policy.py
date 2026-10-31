"""Separate knowledge sharing from agent membership and execution grants."""

from __future__ import annotations

from ..access import AccessDenied, Identity, Membership
from ..dynamo import DynamoRepository
from ..organizations import MAX_ORGANIZATION_DEPTH, OrganizationService
from ..records import identifier
from ..roles import Role
from ..service import ResourceService
from .records import MAX_SHARED_CHILDREN, Document


class KnowledgePolicy:
    def __init__(self, service: ResourceService) -> None:
        self.service = service
        self.repository = DynamoRepository(service.client, service.records_table)
        self.organizations = OrganizationService(service.client, service.records_table)

    def company_member(self, identity: Identity, company: str) -> Membership:
        identifier(company)
        if self.service.platform is not None:
            # Retrieval and publishing are customer operations, not platform support reads.
            if not self.service.platform.company(company)["active"]:
                raise AccessDenied("Access denied")
        member = self.repository.membership(identity, company)
        if member is None:
            raise AccessDenied("Access denied")
        return member

    def scope(
        self, identity: Identity, company: str, organization: str
    ) -> tuple[Membership, Membership, tuple[str, ...]]:
        company_member = self.company_member(identity, company)
        member = self.repository.membership(identity, company, identifier(organization))
        if member is None:
            raise AccessDenied("Access denied")
        return company_member, member, self.lineage(company, organization)

    def lineage(self, company: str, organization: str) -> tuple[str, ...]:
        lineage: list[str] = []
        current: str | None = organization
        while current is not None:
            if current in lineage or len(lineage) >= MAX_ORGANIZATION_DEPTH:
                raise AccessDenied("Access denied")
            lineage.append(identifier(current))
            current = self.organizations.get(company, current)["parent_id"]
        return tuple(lineage)

    def manage(self, identity: Identity, company: str, organization: str | None) -> None:
        member = self.company_member(identity, company)
        if organization is not None:
            self.lineage(company, identifier(organization))
        if member.company_admin:
            return
        if organization is not None:
            local = self.repository.membership(identity, company, organization)
            if local is not None and local.role == Role.ORGANIZATION_ADMIN:
                return
        raise AccessDenied("Access denied")

    def children(
        self, company: str, organization: str | None, children: list[str]
    ) -> tuple[str, ...]:
        if not isinstance(children, list) or len(children) > MAX_SHARED_CHILDREN:
            raise ValueError("Invalid child organizations")
        if organization is None and children:
            raise ValueError("Company knowledge needs no child grants")
        result = tuple(sorted({identifier(child) for child in children}))
        for child in result:
            self.lineage(company, child)
            if (
                child == organization
                or self.organizations.get(company, child)["parent_id"] != organization
            ):
                raise AccessDenied("Only direct child subtrees can be shared")
        return result


def readable(document: Document, company: str, lineage: tuple[str, ...]) -> bool:
    if not document.active or document.company_id != company or not lineage:
        return False
    owner = document.organization_id
    if owner is None or owner == lineage[0]:
        return True
    if owner not in lineage:
        return False
    owner_index = lineage.index(owner)
    return owner_index > 0 and lineage[owner_index - 1] in document.shared_children
