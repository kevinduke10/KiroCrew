"""Authenticated knowledge routes; callers supply selectors, never authorization."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from ..access import ResourceKey
from ..api import PooledApi, _response
from ..records import IDENTIFIER
from .service import KnowledgeService

DOCUMENT = re.compile(rf"/companies/({IDENTIFIER})/knowledge/documents/({IDENTIFIER})(/sharing)?")
QUERY = re.compile(rf"/companies/({IDENTIFIER})/organizations/({IDENTIFIER})/knowledge/query")
AGENT = re.compile(
    rf"/companies/({IDENTIFIER})/organizations/({IDENTIFIER})/spaces/({IDENTIFIER})/agents/({IDENTIFIER})/knowledge/query"
)
MAX_JSON_BYTES = 2 * 1024 * 1024


def payload(event: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    body = event.get("body", "")
    if not isinstance(body, str) or len(body) > MAX_JSON_BYTES * 2:
        raise ValueError("Invalid request")
    raw = (
        base64.b64decode(body, validate=True)
        if event.get("isBase64Encoded")
        else body.encode("utf-8")
    )
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Invalid request")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("Invalid request")
    return data


class KnowledgeApi(PooledApi):
    def __init__(self, *, knowledge: KnowledgeService, **kwargs):
        super().__init__(**kwargs)
        self.knowledge = knowledge

    def accepts_path(self, path: str) -> bool:
        return bool(
            isinstance(path, str) and any(p.fullmatch(path) for p in (DOCUMENT, QUERY, AGENT))
        )

    def dispatch(self, event, identity, method, path, headers):
        document, query, agent = (pattern.fullmatch(path) for pattern in (DOCUMENT, QUERY, AGENT))
        allowed = (
            {"PUT"} if document and document[3] else ({"GET", "PUT"} if document else {"POST"})
        )
        if method not in allowed:
            return _response(405, {"error": "method_not_allowed"})
        if (
            method != "GET"
            and headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return _response(415, {"error": "json_required"})
        if document:
            company, document_id = document[1], document[2]
            if method == "GET":
                return _response(200, self.knowledge.status(identity, company, document_id))
            if document[3]:
                revision = headers.get("if-match", "")
                if not isinstance(revision, str) or not re.fullmatch(rf'"{IDENTIFIER}"', revision):
                    return _response(428, {"error": "revision_required"})
                data = payload(event, {"shared_children", "active"})
                return _response(
                    200,
                    self.knowledge.sharing(
                        identity,
                        company,
                        document_id,
                        data["shared_children"],
                        data["active"],
                        revision[1:-1],
                    ),
                )
            data = payload(
                event, {"organization_id", "title", "extension", "data", "shared_children"}
            )
            if not isinstance(data["data"], str):
                raise ValueError("Invalid document")
            content = base64.b64decode(data["data"], validate=True)
            return _response(
                202,
                self.knowledge.upload(
                    identity,
                    company,
                    document_id,
                    organization=data["organization_id"],
                    title=data["title"],
                    extension=data["extension"],
                    data=content,
                    shared_children=data["shared_children"],
                ),
            )
        data = payload(event, {"query"})
        if agent:
            key = ResourceKey(agent[1], agent[3], agent[4], agent[2])
            return _response(200, self.knowledge.for_agent(identity, key, data["query"]))
        if query:
            return _response(
                200, self.knowledge.search(identity, query[1], query[2], data["query"])
            )
        return _response(404, {"error": "not_found"})
