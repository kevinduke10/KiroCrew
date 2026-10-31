"""Guarded HTTP behavior with real authorization/persistence and injected identity."""

import base64
import json
from dataclasses import replace
from itertools import count
from unittest.mock import Mock

import pytest
from kirocrew_custom.saas.api import LoginConfig, PooledApi
from kirocrew_custom.saas.auth import AuthenticationError
from kirocrew_custom.saas.dynamo import DynamoAudit
from kirocrew_custom.saas.records import member_key
from kirocrew_custom.saas.service import ResourceService
from saas_helpers import ADMIN, ISSUER, KEY, NOW, OWNER, MemoryDynamo


@pytest.fixture
def api():
    client = MemoryDynamo()
    ids = count(1)
    verifier = Mock()
    verifier.verify.return_value = OWNER
    service = ResourceService(
        client=client,
        records_table="records",
        audit=DynamoAudit(client, "audit"),
        clock=lambda: NOW,
        new_id=lambda: f"operation-{next(ids)}",
    )
    login = LoginConfig(
        ISSUER,
        "app-client",
        "https://login.example.test",
        "https://app.example.test/callback",
        "https://app.example.test/",
    )
    return PooledApi(verifier=verifier, service=service, login=login), client, verifier


def event(method="GET", *, headers=None, body=None, company=KEY.company_id):
    return {
        "version": "2.0",
        "requestContext": {"http": {"method": method}},
        "rawPath": f"/companies/{company}/organizations/{KEY.organization_id}/spaces/{KEY.space_id}/resources/{KEY.resource_id}",
        "headers": {"Authorization": "Bearer valid-test-token", **(headers or {})},
        "body": json.dumps(body) if body is not None else "",
    }


def test_read_then_conditional_edit_roundtrip(api):
    app, _, _ = api
    first = app.handle(event())
    assert first["statusCode"] == 200
    assert first["headers"]["etag"] == '"v1"'
    updated = app.handle(
        event(
            "PUT",
            headers={"If-Match": '"v1"', "Content-Type": "application/json"},
            body={"content": {"text": "updated"}},
        )
    )
    assert updated["statusCode"] == 200
    assert json.loads(app.handle(event())["body"])["content"] == {"text": "updated"}
    assert (
        app.handle(
            event(
                "PUT",
                headers={"If-Match": '"v1"', "Content-Type": "application/json"},
                body={"content": {}},
            )
        )["statusCode"]
        == 409
    )


def test_browser_identity_and_admin_fields_cannot_grant_access(api):
    app, _, verifier = api
    verifier.verify.return_value = replace(OWNER, subject="intruder")
    request = event()
    request.update(identity={"subject": OWNER.subject}, administrator=True)
    request["requestContext"]["authorizer"] = {"jwt": {"claims": {"sub": OWNER.subject}}}
    assert app.handle(request)["statusCode"] == 403
    verifier.verify.assert_called_once_with("valid-test-token")


def test_company_selector_is_not_membership(api):
    app, client, _ = api
    assert app.handle(event(company="other-company"))["statusCode"] == 403
    assert len(client.calls) == 1


def test_persisted_revocation_is_checked_on_next_http_request(api):
    app, client, _ = api
    assert app.handle(event())["statusCode"] == 200
    client.items[tuple(member_key(KEY.company_id, OWNER).values())]["active"] = False
    assert app.handle(event())["statusCode"] == 403


@pytest.mark.parametrize("authorization", ["", "Basic abc", "Bearer one two"])
def test_missing_or_malformed_bearer_never_reaches_storage(api, authorization):
    app, client, verifier = api
    response = app.handle(event(headers={"Authorization": authorization}))
    assert response["statusCode"] == 401
    assert response["headers"]["www-authenticate"] == "Bearer"
    assert not client.calls
    verifier.verify.assert_not_called()


def test_authentication_failure_and_upstream_errors_are_redacted(api):
    app, client, verifier = api
    verifier.verify.side_effect = AuthenticationError("secret token")
    assert app.handle(event())["statusCode"] == 401
    verifier.verify.side_effect = None
    verifier.verify.return_value = ADMIN
    client.fail_audit = "completed"
    response = app.handle(event())
    assert response["statusCode"] == 503
    assert "secret" not in response["body"]
    assert "sensitive" not in response["body"]


@pytest.mark.parametrize(
    "payload",
    [
        {"content": {}, "grants": []},
        {"content": {}, "owner": "attacker"},
        {"content": {}, "company_id": "other"},
        ["not an object"],
        {"content": "x" * 65537},
    ],
)
def test_edit_rejects_metadata_replacement_and_large_payload(api, payload):
    app, client, _ = api
    response = app.handle(
        event("PUT", headers={"Content-Type": "application/json", "If-Match": '"v1"'}, body=payload)
    )
    assert response["statusCode"] == 400
    assert not any(kind == "update" for kind, _ in client.calls)


def test_edit_requires_json_and_client_revision(api):
    app, _, _ = api
    assert app.handle(event("PUT", body={"content": {}}))["statusCode"] == 415
    assert (
        app.handle(
            event("PUT", headers={"Content-Type": "application/json"}, body={"content": {}})
        )["statusCode"]
        == 428
    )


def test_base64_body_and_malformed_json(api):
    app, _, _ = api
    request = event("PUT", headers={"Content-Type": "application/json", "If-Match": '"v1"'})
    request.update(isBase64Encoded=True, body=base64.b64encode(b'{"content": {}}').decode("ascii"))
    assert app.handle(request)["statusCode"] == 200
    request["body"] = "not base64!"
    assert app.handle(request)["statusCode"] == 400


def test_managed_login_configuration_is_public_and_does_not_access_storage(api):
    app, client, verifier = api
    request = event()
    request["rawPath"] = "/auth/config"
    request["headers"] = {}
    response = app.handle(request)
    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["response_type"] == "code"
    assert payload["code_challenge_method"] == "S256"
    assert "aws.cognito.signin.user.admin" in payload["scopes"]
    assert not client.calls
    verifier.verify.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/api/status",
        "/companies/a/spaces/b/resources/../c",
        "/companies/a%2Fb/spaces/b/resources/c",
    ],
)
def test_no_personal_gateway_or_path_traversal_fallback(api, path):
    app, client, _ = api
    request = event()
    request["rawPath"] = path
    assert app.handle(request)["statusCode"] == 404
    assert not client.calls


def test_malformed_authoritative_record_is_dependency_failure(api):
    app, client, _ = api
    client.items[tuple(member_key(KEY.company_id, OWNER).values())]["administrator"] = "true"
    response = app.handle(event())
    assert response["statusCode"] == 503
    assert json.loads(response["body"]) == {
        "error": "service_unavailable",
        "code": "service_unavailable",
    }
