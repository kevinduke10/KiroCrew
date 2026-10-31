"""Control-plane HTTP routing, isolated from the content Lambda's write authority."""

from __future__ import annotations

import json
import re
from typing import Any

from .api import PooledApi, _response
from .control import ControlService
from .records import IDENTIFIER
from .roles import Role

COMPANY_PATH = re.compile(rf"/companies/({IDENTIFIER})")
MEMBER_ROLE_PATH = re.compile(rf"/companies/({IDENTIFIER})/members/({IDENTIFIER})")
INVITE_PATH = re.compile(rf"/companies/({IDENTIFIER})/invitations")


class ControlApi(PooledApi):
    def __init__(self, *, control: ControlService, **kwargs):
        super().__init__(**kwargs)
        self.control = control

    def dispatch(self, event: dict[str, Any], identity, method: str, path: str, headers):
        after = (event.get("queryStringParameters") or {}).get("after")
        if path == "/me" and method == "GET":
            return _response(200, self.control.me(identity, after))
        if method not in {"POST", "PUT"}:
            return _response(405, {"error": "method_not_allowed"})
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return _response(415, {"error": "json_required"})
        body = event.get("body", "")
        if event.get("isBase64Encoded") or not isinstance(body, str) or len(body.encode()) > 4096:
            raise ValueError("Invalid request")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Invalid request")
        request_id = headers.get("idempotency-key", "")
        if path == "/platform/companies" and method == "POST" and set(payload) == {"name"}:
            return _response(
                200, self.control.create_company(identity, payload["name"], request_id)
            )
        company = COMPANY_PATH.fullmatch(path)
        if company and method == "PUT" and set(payload) == {"name", "active"}:
            revision = headers.get("if-match", "")
            if not re.fullmatch(rf'"{IDENTIFIER}"', revision):
                return _response(428, {"error": "revision_required"})
            return _response(
                200,
                self.control.update_company(
                    identity,
                    company[1],
                    payload["name"],
                    payload["active"],
                    revision[1:-1],
                    request_id,
                ),
            )
        member = MEMBER_ROLE_PATH.fullmatch(path)
        if member and method == "PUT" and set(payload) == {"role", "active"}:
            revision = headers.get("if-match", "")
            if not re.fullmatch(rf'"{IDENTIFIER}"', revision):
                return _response(428, {"error": "revision_required"})
            return _response(
                200,
                self.control.set_member_role(
                    identity,
                    member[1],
                    member[2],
                    Role(payload["role"]),
                    payload["active"],
                    revision[1:-1],
                ),
            )
        invite = INVITE_PATH.fullmatch(path)
        if (
            invite
            and method == "POST"
            and set(payload) in ({"email", "administrator"}, {"email", "role"})
        ):
            return _response(
                200,
                self.control.invite(
                    identity,
                    invite[1],
                    payload["email"],
                    Role(payload["role"]) if "role" in payload else payload["administrator"],
                    request_id,
                ),
            )
        raise ValueError("Invalid request")
