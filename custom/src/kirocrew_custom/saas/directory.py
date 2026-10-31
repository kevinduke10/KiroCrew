"""Bounded agent discovery using the same exact-organization access boundary."""

from __future__ import annotations

from typing import Any, NoReturn

from .access import AccessDenied, Action, Identity, Membership, ResourceKey, perform
from .dynamo import METADATA_FIELDS, DynamoRepository, decode_item, encode_item
from .records import company_key, parse_snapshot, resource_prefix
from .service import ResourceService

MAX_DIRECTORY_PAGES = 10
DIRECTORY_PAGE_SIZE = 50


def list_agents(
    service: ResourceService, identity: Identity, company: str, organization: str
) -> dict[str, Any]:
    if service.platform is not None:
        service.platform.require_company(identity, company)
    repository = DynamoRepository(service.client, service.records_table)
    initial_membership = _require_membership(repository, identity, company, organization)
    names = {f"#f{i}": field for i, field in enumerate(METADATA_FIELDS)}
    request: dict[str, Any] = {
        "TableName": service.records_table,
        "KeyConditionExpression": "pk = :company AND begins_with(sk, :prefix)",
        "ExpressionAttributeValues": encode_item(
            {":company": company_key(company), ":prefix": resource_prefix(organization)}
        ),
        "ConsistentRead": True,
        "Limit": DIRECTORY_PAGE_SIZE,
        "ProjectionExpression": ",".join(names),
        "ExpressionAttributeNames": names,
    }
    agents = []
    for _ in range(MAX_DIRECTORY_PAGES):
        response = service.client.query(**request)
        for raw in response.get("Items", []):
            item = decode_item(raw)
            key = ResourceKey(company, item["space_id"], item["resource_id"], organization)
            resource = parse_snapshot(item, key).resource
            if resource.kind != "agent_definition" or (
                resource.private and resource.owner != identity
            ):
                continue
            try:
                agents.append(
                    perform(
                        identity=identity,
                        key=key,
                        action=Action.READ,
                        repository=DynamoRepository(service.client, service.records_table),
                        audit=service.audit,
                        operation_id=service.new_id(),
                        clock=service.clock,
                        operation=lambda current: (
                            {
                                "organization_id": current.key.organization_id,
                                "space_id": current.key.space_id,
                                "resource_id": current.key.resource_id,
                            }
                            if (
                                current.kind == "agent_definition"
                                and (not current.private or current.owner == identity)
                            )
                            else _deny()
                        ),
                    )
                )
            except AccessDenied:
                continue
        if not response.get("LastEvaluatedKey"):
            if (
                _require_membership(repository, identity, company, organization)
                != initial_membership
            ):
                raise AccessDenied("Access denied")
            if service.platform is not None:
                service.platform.require_company(identity, company)
            return {"agents": agents}
        request["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    raise RuntimeError("Directory capacity exceeded")


def _deny() -> NoReturn:
    raise AccessDenied("Access denied")


def _require_membership(
    repository: DynamoRepository, identity: Identity, company: str, organization: str
) -> tuple[Membership, Membership]:
    company_member = repository.membership(identity, company)
    organization_member = repository.membership(identity, company, organization)
    if company_member is None or organization_member is None:
        raise AccessDenied("Access denied")
    return company_member, organization_member
