"""Small API Gateway HTTP v2 adapter; no personal-gateway fallback routes."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any

from .access import AccessDenied, ResourceKey
from .auth import USER_SCOPE, AuthenticationError, CognitoVerifier
from .directory import list_agents
from .organizations import OrganizationService
from .records import IDENTIFIER, MAX_CONTENT_BYTES, Conflict, InvalidRecord, canonical
from .roles import Role
from .service import ResourceService

MAX_BODY_BYTES = MAX_CONTENT_BYTES + 1024
RESOURCE_PATH = re.compile(
    rf"/companies/({IDENTIFIER})/organizations/({IDENTIFIER})/spaces/({IDENTIFIER})/resources/({IDENTIFIER})"
)

ORGANIZATION_PATH = re.compile(rf"/companies/({IDENTIFIER})/organizations/({IDENTIFIER})")
MEMBERSHIP_PATH = re.compile(rf"/companies/({IDENTIFIER})/organizations/({IDENTIFIER})/members")
PERMISSIONS_PATH = re.compile(
    rf"/companies/({IDENTIFIER})/organizations/({IDENTIFIER})/permissions"
)
DIRECTORY_PATH = re.compile(rf"/companies/({IDENTIFIER})/organizations/({IDENTIFIER})/agents")


@dataclass(frozen=True)
class LoginConfig:
    issuer: str
    client_id: str
    domain: str
    callback_url: str
    logout_url: str
    knowledge_enabled: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "knowledge_enabled": self.knowledge_enabled,
            "issuer": self.issuer,
            "client_id": self.client_id,
            "authorization_endpoint": self.domain + "/oauth2/authorize",
            "token_endpoint": self.domain + "/oauth2/token",
            "logout_endpoint": self.domain + "/logout",
            "redirect_uri": self.callback_url,
            "logout_uri": self.logout_url,
            "response_type": "code",
            "code_challenge_method": "S256",
            "scopes": ["openid", USER_SCOPE],
        }


def _response(status: int, payload: Any) -> dict[str, Any]:
    if status >= 400 and isinstance(payload, dict) and "error" in payload:
        payload = {**payload, "code": payload["error"]}
    headers = {
        "content-type": "application/json",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
    }
    if status == 401:
        headers["www-authenticate"] = "Bearer"
    if status == 200 and isinstance(payload, dict) and "revision" in payload:
        headers["etag"] = '"' + payload["revision"] + '"'
    return {"statusCode": status, "headers": headers, "body": canonical(payload)}


def _body(
    event: dict[str, Any], fields: set[str] | None = None, optional_fields: set[str] | None = None
) -> dict[str, Any]:
    body = event.get("body", "")
    if not isinstance(body, str) or len(body) > MAX_BODY_BYTES * 2:
        raise ValueError("Invalid request body")
    raw = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") else body.encode()
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("Request body too large")
    payload = json.loads(raw)
    required = fields if fields is not None else {"content"}
    if not isinstance(payload, dict) or (
        set(payload) != required and set(payload) != required | (optional_fields or set())
    ):
        raise ValueError("Only content may be edited")
    return payload


class PooledApi:
    def __init__(
        self, *, verifier: CognitoVerifier, service: ResourceService, login: LoginConfig
    ) -> None:
        self.verifier = verifier
        self.service = service
        self.login = login

    def accepts_path(self, path: str) -> bool:
        return False

    def dispatch(self, event, identity, method, path, headers):
        return _response(404, {"error": "not_found"})

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        try:
            if event.get("version") != "2.0":
                return _response(400, {"error": "unsupported_request"})
            method = event.get("requestContext", {}).get("http", {}).get("method")
            path = event.get("rawPath", "")
            if method == "GET" and path == "/auth/config":
                return _response(200, self.login.public())
            match = RESOURCE_PATH.fullmatch(path) if isinstance(path, str) else None
            org_match = ORGANIZATION_PATH.fullmatch(path) if isinstance(path, str) else None
            member_match = MEMBERSHIP_PATH.fullmatch(path) if isinstance(path, str) else None
            directory_match = DIRECTORY_PATH.fullmatch(path) if isinstance(path, str) else None
            permissions_match = PERMISSIONS_PATH.fullmatch(path) if isinstance(path, str) else None
            control_path = path in {"/me", "/platform/companies"} or (
                isinstance(path, str)
                and re.fullmatch(
                    rf"/companies/{IDENTIFIER}(?:/invitations|/members/{IDENTIFIER})?", path
                )
            )
            if not any(
                (
                    match,
                    org_match,
                    member_match,
                    directory_match,
                    permissions_match,
                    control_path,
                    self.accepts_path(path),
                )
            ):
                return _response(404, {"error": "not_found"})
            if method not in {"GET", "PUT", "POST"}:
                return _response(405, {"error": "method_not_allowed"})
            headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
            authorization = headers.get("authorization", "")
            if not isinstance(authorization, str):
                raise AuthenticationError("Authentication required")
            parts = authorization.split()
            if len(parts) != 2 or parts[0].lower() != "bearer":
                raise AuthenticationError("Authentication required")
            identity = self.verifier.verify(parts[1])
            if not any((match, org_match, member_match, directory_match, permissions_match)):
                return self.dispatch(event, identity, method, path, headers)
            allowed = (
                {"PUT"}
                if org_match or member_match
                else ({"GET"} if directory_match or permissions_match else {"GET", "PUT"})
            )
            if method not in allowed:
                return _response(405, {"error": "method_not_allowed"})
            organizations = OrganizationService(
                self.service.client,
                self.service.records_table,
                self.service.platform,
                admin_check=self.service.platform.require_admin if self.service.platform else None,
            )
            if permissions_match:
                return _response(
                    200,
                    organizations.permissions(identity, permissions_match[1], permissions_match[2]),
                )
            if directory_match:
                if self.service.platform is not None:
                    self.service.platform.require_company(identity, directory_match[1])
                return _response(
                    200, list_agents(self.service, identity, *directory_match.groups())
                )
            if org_match or member_match:
                if (
                    headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    != "application/json"
                ):
                    return _response(415, {"error": "json_required"})
                if org_match:
                    payload = _body(event, {"name", "parent_id"})
                    return _response(
                        201,
                        self.service.manage_organization(
                            identity,
                            org_match[1],
                            org_match[2],
                            lambda: organizations.create(
                                identity,
                                org_match[1],
                                org_match[2],
                                payload["name"],
                                payload["parent_id"],
                            ),
                        ),
                    )
                assert member_match is not None
                payload = _body(event, {"subject", "active", "groups"}, {"role"})
                return _response(
                    200,
                    self.service.manage_organization(
                        identity,
                        member_match[1],
                        member_match[2],
                        lambda: organizations.set_member(
                            identity,
                            member_match[1],
                            member_match[2],
                            payload["subject"],
                            payload["active"],
                            payload["groups"],
                            Role(payload.get("role", "member")),
                        ),
                        membership=True,
                        details=(
                            ("subject", payload["subject"]),
                            ("role", Role(payload.get("role", "member")).value),
                            ("active", str(payload["active"]).lower()),
                        ),
                    ),
                )
            assert match is not None
            company, organization, space, resource = match.groups()
            key = ResourceKey(company, space, resource, organization)
            if method == "GET":
                return _response(200, self.service.read(identity, key))
            if (
                headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                return _response(415, {"error": "json_required"})
            revision = headers.get("if-match", "")
            if not isinstance(revision, str) or not re.fullmatch(rf'"{IDENTIFIER}"', revision):
                return _response(428, {"error": "revision_required"})
            payload = _body(event)
            return _response(
                200, self.service.edit(identity, key, payload["content"], revision[1:-1])
            )
        except AuthenticationError:
            return _response(401, {"error": "authentication_required"})
        except AccessDenied:
            return _response(403, {"error": "access_denied"})
        except Conflict:
            return _response(409, {"error": "resource_changed"})
        except InvalidRecord:
            return _response(503, {"error": "service_unavailable"})
        except (ValueError, TypeError, binascii.Error, RecursionError):
            return _response(400, {"error": "invalid_request"})
        except Exception:
            return _response(503, {"error": "service_unavailable"})
