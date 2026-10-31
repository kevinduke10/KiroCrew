"""Superman authorization, tenant boundaries and audited lifecycle operations."""

from dataclasses import replace
from itertools import count
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from kirocrew_custom.saas.access import AccessDenied, Membership, Resource
from kirocrew_custom.saas.control import ControlService
from kirocrew_custom.saas.dynamo import DynamoAudit
from kirocrew_custom.saas.platform import PlatformDirectory, platform_role_item
from kirocrew_custom.saas.records import Conflict, InvalidRecord, membership_item, resource_item
from kirocrew_custom.saas.service import ResourceService
from platform_helpers import SUPERMAN, PlatformDynamo
from saas_helpers import ADMIN, ISSUER, KEY, NOW, OWNER, PEER, client_error


@pytest.fixture
def platform():
    client = PlatformDynamo()
    directory = PlatformDirectory(
        client, roles_table="roles", control_table="control", records_table="records"
    )
    audit = DynamoAudit(client, "audit")
    ids = count(1)

    def new_id():
        return f"operation-{next(ids)}"

    cognito = Mock()
    cognito.admin_create_user.return_value = {
        "User": {"Attributes": [{"Name": "sub", "Value": "invited-user"}]}
    }
    control = ControlService(
        directory=directory,
        audit=audit,
        cognito=cognito,
        pool_id="us-east-1_TestPool",
        issuer=ISSUER,
        clock=lambda: NOW,
        new_id=new_id,
    )
    resources = ResourceService(
        client=client,
        records_table="records",
        audit=audit,
        clock=lambda: NOW,
        new_id=new_id,
        platform=directory,
    )
    return client, directory, control, resources, cognito


def test_superman_reads_and_edits_across_companies_with_audit(platform):
    client, _, _, resources, _ = platform
    other = replace(KEY, company_id="company-b")
    client.put(resource_item(Resource(other, "conversation", True, OWNER), "v1", {"company": "b"}))
    for key in (KEY, other):
        assert resources.read(SUPERMAN, key)
        assert resources.edit(SUPERMAN, key, {"support": "edit"}, "v1")
    assert len(client.audit) == 8
    assert {item["action"] for item in client.audit} == {"read", "edit"}
    assert {item["resource"]["company_id"] for item in client.audit} == {"company-a", "company-b"}


def test_company_admin_has_no_global_authority(platform):
    _, _, control, resources, _ = platform
    with pytest.raises(AccessDenied):
        control.create_company(ADMIN, "Forbidden", "request-1")
    with pytest.raises(AccessDenied):
        control.update_company(ADMIN, "company-b", "Forbidden", False, "v1")
    with pytest.raises(AccessDenied):
        resources.read(ADMIN, replace(KEY, company_id="company-b"))


def test_platform_grant_revocation_applies_to_next_operation(platform):
    client, _, control, resources, _ = platform
    assert resources.read(SUPERMAN, KEY)
    client.seed("roles", platform_role_item(SUPERMAN, active=False))
    with pytest.raises(AccessDenied):
        resources.read(SUPERMAN, KEY)
    with pytest.raises(AccessDenied):
        control.create_company(SUPERMAN, "Forbidden", "request-1")


def test_audits_fail_closed_before_mutation_or_content(platform):
    client, _, control, resources, cognito = platform
    client.fail_audit = "authorized_before_access"
    for operation in (
        lambda: control.create_company(SUPERMAN, "Example", "request-1"),
        lambda: resources.read(SUPERMAN, KEY),
        lambda: control.invite(ADMIN, "company-a", "user@example.test", False, "invite-1"),
    ):
        with pytest.raises(ClientError):
            operation()
    assert client.transactions == 0
    cognito.admin_create_user.assert_not_called()


def test_create_company_is_atomic_idempotent_and_has_root_organization(platform):
    client, directory, control, _, _ = platform
    first = control.create_company(SUPERMAN, "Example", "request-1")
    assert control.create_company(SUPERMAN, "Example", "request-1") == first
    assert client.transactions == 1
    assert directory.membership(SUPERMAN, first["company_id"]).administrator is True
    root = client.tables["records"]["ORG#" + first["company_id"], "root"]
    assert root["parent_id"] is None
    with pytest.raises(Conflict):
        control.create_company(SUPERMAN, "Different name", "request-1")


def test_audit_completion_failure_can_retry_without_duplicate_company(platform):
    client, _, control, _, _ = platform
    client.fail_audit = "completed"
    with pytest.raises(ClientError):
        control.create_company(SUPERMAN, "Example", "request-1")
    client.fail_audit = None
    assert control.create_company(SUPERMAN, "Example", "request-1")["name"] == "Example"
    assert client.transactions == 1


def test_suspension_denies_ordinary_content_but_allows_audited_platform_support(platform):
    _, _, control, resources, _ = platform
    result = control.update_company(SUPERMAN, "company-a", "Renamed", False, "v1")
    with pytest.raises(AccessDenied):
        resources.read(OWNER, KEY)
    assert resources.read(SUPERMAN, KEY)
    with pytest.raises(Conflict):
        control.update_company(SUPERMAN, "company-a", "Wrong", True, "v1")
    control.update_company(SUPERMAN, "company-a", "Renamed", True, result["revision"])
    assert resources.read(OWNER, KEY)


def test_invitation_is_scoped_idempotent_and_cannot_rewrite_existing_role(platform):
    client, directory, control, _, cognito = platform
    result = control.invite(SUPERMAN, "company-a", "user@example.test", True, "invite-1")
    assert control.invite(SUPERMAN, "company-a", "user@example.test", True, "invite-1") == result
    assert cognito.admin_create_user.call_count == 1
    identity = replace(OWNER, subject=result["subject"])
    assert directory.membership(identity, "company-a").administrator is True
    assert directory.is_superman(identity) is False
    with pytest.raises(Conflict):
        control.invite(SUPERMAN, "company-a", "user@example.test", False, "invite-1")
    with pytest.raises(Conflict):
        control.invite(SUPERMAN, "company-a", "user@example.test", False, "invite-2")
    assert directory.membership(identity, "company-a").administrator is True


def test_invitation_reconciles_cognito_success_after_storage_failure(platform):
    client, directory, control, _, cognito = platform
    client.fail_transaction = True
    with pytest.raises(ClientError):
        control.invite(ADMIN, "company-a", "user@example.test", False, "invite-1")
    client.fail_transaction = False
    cognito.admin_create_user.side_effect = client_error("UsernameExistsException")
    cognito.admin_get_user.return_value = {
        "UserAttributes": [{"Name": "sub", "Value": "invited-user"}]
    }
    assert (
        control.invite(ADMIN, "company-a", "user@example.test", False, "invite-1")["status"]
        == "invited"
    )
    assert directory.membership(replace(OWNER, subject="invited-user"), "company-a") is not None


@pytest.mark.parametrize("actor,company", [(PEER, "company-a"), (ADMIN, "company-b")])
def test_unauthorized_invitation_never_calls_cognito(platform, actor, company):
    _, _, control, _, cognito = platform
    with pytest.raises(AccessDenied):
        control.invite(actor, company, "user@example.test", True, "invite-1")
    cognito.admin_create_user.assert_not_called()


def test_directory_revalidates_membership_and_does_not_disclose_other_company(platform):
    client, _, control, _, _ = platform
    assert [item["company_id"] for item in control.me(OWNER)["companies"]] == ["company-a"]
    client.put(membership_item(Membership(OWNER, "company-a"), active=False))
    assert control.me(OWNER)["companies"] == []
    assert {item["company_id"] for item in control.me(SUPERMAN)["companies"]} == {
        "company-a",
        "company-b",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"active": "true"},
        {"role": "company_admin"},
        {"schema": True},
        {"identity": {"issuer": "evil", "subject": "operator"}},
    ],
)
def test_malformed_platform_grant_fails_closed(platform, change):
    client, directory, _, _, _ = platform
    client.seed("roles", {**platform_role_item(SUPERMAN, active=True), **change})
    with pytest.raises(InvalidRecord):
        directory.is_superman(SUPERMAN)


def test_control_http_authentication_cannot_accept_a_browser_superman_claim(platform):
    import json

    from kirocrew_custom.saas.api import LoginConfig
    from kirocrew_custom.saas.control_api import ControlApi

    client, _, control, resources, _ = platform
    verifier = Mock()
    verifier.verify.return_value = ADMIN
    api = ControlApi(
        control=control,
        verifier=verifier,
        service=resources,
        login=LoginConfig(
            ISSUER,
            "client",
            "https://login.example.test",
            "https://app.example.test/",
            "https://app.example.test/",
        ),
    )
    request = {
        "version": "2.0",
        "requestContext": {"http": {"method": "POST"}, "authorizer": {"superman": True}},
        "rawPath": "/platform/companies",
        "headers": {
            "Authorization": "Bearer token",
            "Content-Type": "application/json",
            "Idempotency-Key": "request-1",
            "X-Superman": "true",
        },
        "body": json.dumps({"name": "Example"}),
    }
    assert api.handle(request)["statusCode"] == 403
    assert client.transactions == 0
    verifier.verify.return_value = SUPERMAN
    created = api.handle(request)
    assert created["statusCode"] == 200
    company = json.loads(created["body"])
    request.update(
        rawPath="/companies/" + company["company_id"],
        body=json.dumps({"name": "Changed", "active": False}),
    )
    request["requestContext"]["http"]["method"] = "PUT"
    request["headers"]["If-Match"] = '"' + company["revision"] + '"'
    assert api.handle(request)["statusCode"] == 200


def test_org_mutations_allow_audited_superman_without_customer_membership(platform):
    from kirocrew_custom.saas.organizations import OrganizationService

    client, directory, _, resources, _ = platform
    organizations = OrganizationService(client, "records", admin_check=directory.require_admin)
    original = client.put_item

    def put(**request):
        if request["TableName"] == "records":
            from kirocrew_custom.saas.dynamo import decode_item

            item = decode_item(request["Item"])
            client.seed("records", item)
            return {}
        return original(**request)

    client.put_item = put
    result = resources.manage_organization(
        SUPERMAN,
        "company-b",
        "hr",
        lambda: organizations.create(SUPERMAN, "company-b", "hr", "HR", None),
    )
    assert result["organization_id"] == "hr"
    assert [item["outcome"] for item in client.audit] == ["authorized_before_access", "completed"]
    assert client.audit[0]["resource"]["organization_id"] == "hr"


def test_real_org_http_route_admits_superman_through_audited_path(platform):
    import json

    from kirocrew_custom.saas.api import LoginConfig, PooledApi
    from kirocrew_custom.saas.dynamo import decode_item

    client, _, _, resources, _ = platform
    original = client.put_item

    def put(**request):
        if request["TableName"] == "records":
            client.seed("records", decode_item(request["Item"]))
            return {}
        return original(**request)

    client.put_item = put
    verifier = Mock()
    verifier.verify.return_value = SUPERMAN
    api = PooledApi(
        verifier=verifier,
        service=resources,
        login=LoginConfig(
            ISSUER,
            "client",
            "https://login.example.test",
            "https://app.example.test/",
            "https://app.example.test/",
        ),
    )
    response = api.handle(
        {
            "version": "2.0",
            "requestContext": {"http": {"method": "PUT"}},
            "rawPath": "/companies/company-b/organizations/hr",
            "headers": {"Authorization": "Bearer token", "Content-Type": "application/json"},
            "body": json.dumps({"name": "HR", "parent_id": None}),
        }
    )
    assert response["statusCode"] == 201
    assert [item["outcome"] for item in client.audit] == ["authorized_before_access", "completed"]


def test_company_transaction_and_invitation_sdk_shapes_are_valid(platform):
    import boto3
    from botocore.stub import Stubber

    client, _, control, _, _ = platform
    control.create_company(SUPERMAN, "Example", "request-sdk")
    control.invite(ADMIN, "company-a", "new@example.test", False, "invite-sdk")
    requests = [request for kind, request in client.calls if kind == "transaction"]
    sdk = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    with Stubber(sdk) as stub:
        for request in requests:
            stub.add_response("transact_write_items", {}, request)
            sdk.transact_write_items(**request)
        stub.assert_no_pending_responses()


def test_company_update_retry_uses_atomic_receipt_and_rejects_reused_key(platform):
    client, _, control, _, _ = platform
    first = control.update_company(SUPERMAN, "company-a", "Renamed", False, "v1", "update-1")
    assert (
        control.update_company(SUPERMAN, "company-a", "Renamed", False, "v1", "update-1") == first
    )
    assert client.transactions == 1
    with pytest.raises(Conflict):
        control.update_company(SUPERMAN, "company-a", "Different", False, "v1", "update-1")
