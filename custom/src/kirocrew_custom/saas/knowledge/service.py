"""Audited upload/sharing and authorization before and after vector retrieval."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from typing import Any, Callable, TypedDict, TypeVar

from ..access import AccessDenied, Action, AuditEvent, Identity, ResourceKey, perform
from ..dynamo import DynamoRepository
from ..records import Conflict, canonical, identifier
from ..service import ResourceService
from .bedrock import BedrockKnowledge, validate_upload
from .policy import KnowledgePolicy, readable
from .records import Document, KnowledgeStore

T = TypeVar("T")
MAX_QUERY_CHARS = 2000
MAX_EXCERPT_CHARS = 4000
MAX_RESULTS = 5


class Citation(TypedDict):
    document_id: str
    title: str
    organization_id: str | None
    score: float
    source_version: str
    excerpt: str


class KnowledgeService:
    def __init__(
        self, resources: ResourceService, store: KnowledgeStore, backend: BedrockKnowledge
    ) -> None:
        self.resources, self.store, self.backend = resources, store, backend
        self.policy = KnowledgePolicy(resources)

    def _audited(self, identity: Identity, document: Document, operation: Callable[[], T]) -> T:
        self.policy.manage(identity, document.company_id, document.organization_id)
        event = AuditEvent(
            self.resources.new_id(),
            identity,
            ResourceKey(
                document.company_id,
                "knowledge-organization" if document.organization_id else "knowledge-company",
                document.document_id,
                document.organization_id or "company",
            ),
            Action.MANAGE,
            self.resources.clock(),
        )
        if not self.resources.audit.record_access(event):
            raise RuntimeError("Audit unavailable")
        try:
            result = operation()
        except Exception:
            self.resources.audit.record_outcome(
                replace(event, outcome="failed", occurred_at=self.resources.clock())
            )
            raise
        if not self.resources.audit.record_outcome(
            replace(event, outcome="completed", occurred_at=self.resources.clock())
        ):
            raise RuntimeError("Audit unavailable")
        return result

    def upload(
        self,
        identity: Identity,
        company: str,
        document_id: str,
        *,
        organization: str | None,
        title: str,
        extension: str,
        data: bytes,
        shared_children: list[str],
    ) -> dict[str, Any]:
        identifier(document_id)
        self.policy.manage(identity, company, organization)
        validate_upload(extension, data)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise ValueError("Invalid document title")
        children = self.policy.children(company, organization, shared_children)
        digest = hashlib.sha256(data).hexdigest()
        fingerprint = hashlib.sha256(
            canonical(
                [company, document_id, organization, title.strip(), extension, digest, children]
            ).encode("utf-8")
        ).hexdigest()
        document = Document(
            company,
            document_id,
            organization,
            title.strip(),
            extension,
            digest,
            fingerprint,
            self.resources.new_id(),
            children,
        )

        def upload() -> dict[str, Any]:
            existing = self.store.reserve(document)
            self.policy.manage(identity, company, existing.organization_id)
            if not existing.active:
                raise Conflict("Knowledge source is disabled")
            status = self.backend.ingest(existing, data, self.resources.new_id())
            return {**existing.public(), "index_status": status}

        return self._audited(identity, document, upload)

    def sharing(
        self,
        identity: Identity,
        company: str,
        document_id: str,
        children: list[str],
        active: bool,
        revision: str,
    ) -> dict[str, Any]:
        document = self.store.get(company, document_id)
        if document is None:
            raise AccessDenied("Access denied")
        self.policy.manage(identity, company, document.organization_id)
        if type(active) is not bool:
            raise ValueError("Invalid active flag")
        if document.revision != revision:
            raise Conflict("Knowledge permissions changed")
        shares = self.policy.children(company, document.organization_id, children)
        return self._audited(
            identity,
            document,
            lambda: self.store.share(document, shares, active, self.resources.new_id()).public(),
        )

    def status(self, identity: Identity, company: str, document_id: str) -> dict[str, Any]:
        document = self.store.get(company, document_id)
        if document is None:
            raise AccessDenied("Access denied")
        self.policy.manage(identity, company, document.organization_id)
        result = {**document.public(), "index_status": self.backend.status(document)}
        self.policy.manage(identity, company, document.organization_id)
        return result

    def search(
        self, identity: Identity, company: str, organization: str, query: str
    ) -> dict[str, Any]:
        initial_scope = self.policy.scope(identity, company, organization)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY_CHARS:
            raise ValueError("Invalid knowledge query")
        allowed = {
            doc.document_id: doc
            for doc in self.store.documents(company)
            if readable(doc, company, initial_scope[2])
        }
        candidates = self.backend.retrieve(company, sorted(allowed), query)
        results: list[Citation] = []
        for candidate in candidates:
            metadata = candidate.get("metadata", {})
            document_id = metadata.get("document_id")
            if not isinstance(document_id, str) or document_id not in allowed:
                continue
            document = self.store.get(company, document_id)
            if (
                document is None
                or document != allowed[document_id]
                or any(metadata.get(k) != v for k, v in self.backend.metadata(document).items())
                or candidate.get("location", {}).get("s3Location", {}).get("uri")
                != self.backend.uri(document)
            ):
                continue
            content = candidate.get("content", {})
            text, score = content.get("text"), candidate.get("score", 0)
            if (
                not isinstance(text, str)
                or content.get("type", "TEXT") != "TEXT"
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                continue
            results.append(
                {
                    "document_id": document_id,
                    "title": document.title,
                    "organization_id": document.organization_id,
                    "score": float(score),
                    "source_version": document.source_version,
                    "excerpt": text[:MAX_EXCERPT_CHARS],
                }
            )
        final_scope = self.policy.scope(identity, company, organization)
        if final_scope != initial_scope:
            raise AccessDenied("Knowledge permissions changed")
        # Recheck every returned source after all external retrieval calls complete.
        results = [
            result
            for result in results
            if (current := self.store.get(company, result["document_id"]))
            == allowed[result["document_id"]]
            and current is not None
            and readable(current, company, final_scope[2])
        ]
        results.sort(key=lambda item: (-item["score"], item["document_id"], item["excerpt"]))
        return {"sources": results[:MAX_RESULTS], "context_kind": "untrusted_retrieved_documents"}

    def for_agent(self, identity: Identity, key: ResourceKey, query: str) -> dict[str, Any]:
        def authorize(operation):
            def agent_operation(resource):
                if resource.kind != "agent_definition":
                    raise AccessDenied("Agent required")
                return operation(resource)

            return perform(
                identity=identity,
                key=key,
                action=Action.RUN,
                repository=DynamoRepository(self.resources.client, self.resources.records_table),
                audit=self.resources.audit,
                operation_id=self.resources.new_id(),
                clock=self.resources.clock,
                operation=agent_operation,
            )

        result = authorize(
            lambda resource: self.search(identity, key.company_id, key.organization_id, query)
        )
        return authorize(lambda resource: result)
