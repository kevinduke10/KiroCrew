"""HTTP ingestion and retrieval checks with real application authorization."""

import base64
import json

import pytest
from kirocrew_custom.saas.knowledge.bedrock import MAX_UPLOAD_BYTES, validate_upload
from knowledge_helpers import setup_knowledge
from saas_helpers import ADMIN, KEY, OWNER


@pytest.fixture
def knowledge_api():
    return setup_knowledge()


def event(path, method="PUT", body=None, headers=None):
    return {
        "version": "2.0",
        "rawPath": f"/companies/{KEY.company_id}/" + path,
        "requestContext": {"http": {"method": method}},
        "headers": {
            "Authorization": "Bearer test",
            "Content-Type": "application/json",
            **(headers or {}),
        },
        "body": json.dumps(body) if body is not None else "",
    }


def upload_body(**changes):
    return {
        "organization_id": None,
        "title": "Company handbook",
        "extension": "txt",
        "data": base64.b64encode(b"Company policy").decode("ascii"),
        "shared_children": [],
        **changes,
    }


def test_upload_status_share_revoke_and_preview_roundtrip(knowledge_api):
    _, cloud, _, app, verifier = knowledge_api
    uploaded = app.handle(
        event(
            "knowledge/documents/handbook",
            body=upload_body(organization_id="hr", shared_children=["recruiting"]),
        )
    )
    assert uploaded["statusCode"] == 202
    revision = json.loads(uploaded["body"])["revision"]
    status = app.handle(event("knowledge/documents/handbook", "GET"))
    assert json.loads(status["body"])["index_status"] == "INDEXED"
    verifier.verify.return_value = OWNER
    query = event("organizations/interns/knowledge/query", "POST", {"query": "policy"})
    assert json.loads(app.handle(query)["body"])["sources"][0]["title"] == "Company handbook"
    verifier.verify.return_value = ADMIN
    revoked = app.handle(
        event(
            "knowledge/documents/handbook/sharing",
            body={"shared_children": [], "active": True},
            headers={"If-Match": '"' + revision + '"'},
        )
    )
    assert revoked["statusCode"] == 200
    assert cloud.results
    verifier.verify.return_value = OWNER
    assert json.loads(app.handle(query)["body"])["sources"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"company_id": "foreign"},
        {"administrator": True},
        {"data": "invalid base64"},
        {"extension": "exe"},
        {"organization_id": "missing"},
        {"shared_children": ["hr"]},
    ],
)
def test_upload_cannot_inject_authority_or_unsupported_data(knowledge_api, changes):
    _, cloud, _, app, _ = knowledge_api
    response = app.handle(event("knowledge/documents/bad", body=upload_body(**changes)))
    assert response["statusCode"] in {400, 403}
    assert not cloud.objects


def test_query_identity_membership_and_filter_are_not_browser_fields(knowledge_api):
    _, cloud, _, app, verifier = knowledge_api
    verifier.verify.return_value = OWNER
    for body in [
        {"query": "policy", "filter": {}},
        {"query": "policy", "identity": {"subject": ADMIN.subject}},
    ]:
        assert (
            app.handle(event("organizations/hr/knowledge/query", "POST", body))["statusCode"] == 400
        )
    assert not cloud.requests
    assert app.handle(event("knowledge/documents/bad", body=upload_body()))["statusCode"] == 403
    assert (
        app.handle(
            event(
                "organizations/hr/knowledge/query",
                "POST",
                {"query": "x"},
                headers={"Authorization": ""},
            )
        )["statusCode"]
        == 401
    )


def test_upstream_failure_is_redacted_and_retry_can_resume(knowledge_api):
    _, cloud, _, app, _ = knowledge_api
    cloud.fail_ingest = True
    response = app.handle(event("knowledge/documents/retry", body=upload_body()))
    assert response["statusCode"] == 503
    assert "sensitive" not in response["body"]
    cloud.fail_ingest = False
    assert app.handle(event("knowledge/documents/retry", body=upload_body()))["statusCode"] == 202


def test_sharing_requires_revision_and_methods_are_limited(knowledge_api):
    _, _, _, app, _ = knowledge_api
    assert (
        app.handle(
            event("knowledge/documents/test/sharing", body={"shared_children": [], "active": False})
        )["statusCode"]
        == 428
    )
    assert app.handle(event("organizations/hr/knowledge/query", "GET"))["statusCode"] == 405
    assert (
        app.handle(
            event("knowledge/documents/test", "PUT", upload_body(), {"Content-Type": "text/plain"})
        )["statusCode"]
        == 415
    )


@pytest.mark.parametrize(
    "extension,data",
    [
        ("txt", b""),
        ("txt", b"\xff"),
        ("md", b"\x00"),
        ("pdf", b"not a pdf"),
        ("txt", b"x" * (MAX_UPLOAD_BYTES + 1)),
    ],
    ids=["empty", "encoding", "nul", "pdf", "oversized"],
)
def test_invalid_or_oversized_documents_are_rejected(extension, data):
    with pytest.raises(ValueError):
        validate_upload(extension, data)
