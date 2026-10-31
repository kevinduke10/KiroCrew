"""Authoritative knowledge manifests; index metadata never grants access."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from botocore.exceptions import ClientError

from ..dynamo import decode_item, encode_item
from ..records import Conflict, InvalidRecord, company_key, identifier

MAX_DOCUMENT_PAGES = 4
DOCUMENT_PAGE_SIZE = 32
MAX_SHARED_CHILDREN = 32


@dataclass(frozen=True)
class Document:
    company_id: str
    document_id: str
    organization_id: str | None
    title: str
    extension: str
    source_version: str
    fingerprint: str
    revision: str
    shared_children: tuple[str, ...] = ()
    active: bool = True

    def public(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if key not in {"fingerprint", "source_version"}
        }


def document_key(company: str, document: str) -> dict[str, str]:
    return {"pk": company_key(company), "sk": "DOCUMENT#" + identifier(document)}


def document_item(document: Document) -> dict[str, Any]:
    return {
        **asdict(document),
        **document_key(document.company_id, document.document_id),
        "shared_children": list(document.shared_children),
        "schema": 1,
        "type": "knowledge",
    }


def parse_document(item: dict[str, Any], company: str, document: str) -> Document:
    try:
        children = item["shared_children"]
        if (
            any(item[k] != v for k, v in document_key(company, document).items())
            or item["company_id"] != company
            or item["document_id"] != document
            or item["schema"] != 1
            or isinstance(item["schema"], bool)
            or item["type"] != "knowledge"
            or type(item["active"]) is not bool
            or not isinstance(item["title"], str)
            or not 1 <= len(item["title"].strip()) <= 200
            or item["extension"] not in {"txt", "md", "pdf"}
            or not isinstance(children, list)
            or len(children) > MAX_SHARED_CHILDREN
            or (item["organization_id"] is None and children)
        ):
            raise InvalidRecord("Invalid knowledge manifest")
        organization = item["organization_id"]
        if organization is not None:
            identifier(organization)
        return Document(
            company,
            document,
            organization,
            item["title"],
            item["extension"],
            identifier(item["source_version"]),
            identifier(item["fingerprint"]),
            identifier(item["revision"]),
            tuple(identifier(c) for c in children),
            item["active"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRecord("Invalid knowledge manifest") from exc


class KnowledgeStore:
    def __init__(self, client: Any, table: str) -> None:
        self.client = client
        self.table = table

    def get(self, company: str, document: str) -> Document | None:
        response = self.client.get_item(
            TableName=self.table,
            Key=encode_item(document_key(company, document)),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return parse_document(decode_item(item), company, document) if item else None

    def reserve(self, document: Document) -> Document:
        try:
            self.client.put_item(
                TableName=self.table,
                Item=encode_item(document_item(document)),
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
            return document
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
        existing = self.get(document.company_id, document.document_id)
        if existing is None or existing.fingerprint != document.fingerprint:
            raise Conflict("Document ID is already in use")
        return existing

    def share(
        self, document: Document, children: tuple[str, ...], active: bool, revision: str
    ) -> Document:
        try:
            self.client.update_item(
                TableName=self.table,
                Key=encode_item(document_key(document.company_id, document.document_id)),
                UpdateExpression="SET shared_children = :children, active = :active, revision = :next",
                ConditionExpression="revision = :expected",
                ExpressionAttributeValues=encode_item(
                    {
                        ":children": list(children),
                        ":active": active,
                        ":next": revision,
                        ":expected": document.revision,
                    }
                ),
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise Conflict("Knowledge permissions changed") from exc
            raise
        return replace(document, shared_children=children, active=active, revision=revision)

    def documents(self, company: str) -> list[Document]:
        request: dict[str, Any] = dict(
            TableName=self.table,
            ConsistentRead=True,
            KeyConditionExpression="pk = :company AND begins_with(sk, :prefix)",
            ExpressionAttributeValues=encode_item(
                {":company": company_key(company), ":prefix": "DOCUMENT#"}
            ),
            Limit=DOCUMENT_PAGE_SIZE,
        )
        documents = []
        for _ in range(MAX_DOCUMENT_PAGES):
            response = self.client.query(**request)
            for raw in response.get("Items", []):
                item = decode_item(raw)
                documents.append(parse_document(item, company, item["document_id"]))
            if not response.get("LastEvaluatedKey"):
                return documents
            request["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        raise RuntimeError("Knowledge catalog capacity exceeded")
