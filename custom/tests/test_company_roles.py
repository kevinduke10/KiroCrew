"""Role matrix, legacy decoding, ownership transfer and scoped administration."""

import json
from unittest.mock import Mock

import pytest
from kirocrew_custom.saas.access import AccessDenied, Identity, Membership
from kirocrew_custom.saas.api import LoginConfig
from kirocrew_custom.saas.control_api import ControlApi
from kirocrew_custom.saas.organizations import OrganizationService, organization_key
from kirocrew_custom.saas.records import (
    Conflict,
    InvalidRecord,
    membership_item,
    parse_membership,
)
from kirocrew_custom.saas.roles import Role
from platform_helpers import SUPERMAN
from saas_helpers import ADMIN, ISSUER, KEY, OWNER, PEER
from test_platform_administration import platform  # noqa: F401


@pytest.fixture
def roles(request):
    state = request.getfixturevalue("platform")
    client, directory, control, resources, cognito = state
    company = directory.company("company-a")
    client.seed("control", {**company, "owner": {"issuer": OWNER.issuer, "subject": OWNER.subject}})
    client.put(membership_item(Membership(OWNER, "company-a", role=Role.COMPANY_OWNER)))
    client.seed(
        "records",
        {
            **organization_key("company-a", "hr"),
            "schema": 1,
            "type": "organization",
            "company_id": "company-a",
            "organization_id": "hr",
            "parent_id": None,
            "name": "HR",
        },
    )
    return state


@pytest.mark.parametrize(
    "role,organization",
    [
        (Role.COMPANY_OWNER, ""),
        (Role.COMPANY_ADMIN, ""),
        (Role.MEMBER, ""),
        (Role.ORGANIZATION_ADMIN, "hr"),
        (Role.MEMBER, "hr"),
    ],
)
def test_role_storage_roundtrip_and_scope(role, organization):
    member = Membership(PEER, "company-a", organization_id=organization, role=role)
    item = membership_item(member)
    assert item["role"] == role.value
    assert parse_membership(item, PEER, "company-a", organization) == member
    assert member.company_admin == (role in {Role.COMPANY_OWNER, Role.COMPANY_ADMIN})


@pytest.mark.parametrize(
    "administrator,organization,expected",
    [(True, "", Role.COMPANY_ADMIN), (False, "", Role.MEMBER), (False, "hr", Role.MEMBER)],
)
def test_legacy_roles_do_not_create_ownership(administrator, organization, expected):
    item = membership_item(
        Membership(PEER, "company-a", administrator=administrator, organization_id=organization)
    )
    item.pop("role")
    assert parse_membership(item, PEER, "company-a", organization).role == expected


@pytest.mark.parametrize("role", ["superman", "builder", "organization_admin", None])
def test_unknown_or_wrong_company_roles_fail_closed(role):
    item = membership_item(Membership(PEER, "company-a"))
    item["role"] = role
    with pytest.raises(InvalidRecord):
        parse_membership(item, PEER, "company-a")


def test_administrator_flag_cannot_contradict_role():
    item = membership_item(Membership(PEER, "company-a", role=Role.COMPANY_OWNER))
    item["administrator"] = False
    with pytest.raises(InvalidRecord):
        parse_membership(item, PEER, "company-a")
    with pytest.raises(ValueError):
        Membership(PEER, "company-a", organization_id="hr", role=Role.COMPANY_ADMIN)


def test_creation_assigns_owner_not_just_admin(roles):
    _, directory, control, _, _ = roles
    created = control.create_company(SUPERMAN, "Example", "owner-request")
    assert directory.membership(SUPERMAN, created["company_id"]).role == Role.COMPANY_OWNER
    assert directory.company(created["company_id"])["owner"]["subject"] == SUPERMAN.subject


def test_only_owner_can_appoint_company_admin_and_owner_transfer_is_atomic(roles):
    client, directory, control, _, _ = roles
    with pytest.raises(AccessDenied):
        control.set_member_role(ADMIN, "company-a", PEER.subject, Role.COMPANY_ADMIN, True, "v1")
    promoted = control.set_member_role(
        OWNER, "company-a", PEER.subject, Role.COMPANY_ADMIN, True, "v1"
    )
    assert directory.membership(PEER, "company-a").role == Role.COMPANY_ADMIN
    transferred = control.set_member_role(
        OWNER, "company-a", PEER.subject, Role.COMPANY_OWNER, True, promoted["revision"]
    )
    assert directory.membership(PEER, "company-a").role == Role.COMPANY_OWNER
    assert directory.membership(OWNER, "company-a").role == Role.COMPANY_ADMIN
    assert directory.company("company-a")["owner"]["subject"] == PEER.subject
    assert (
        len(
            [
                i
                for i in client.items.values()
                if i.get("role") == "company_owner" and i["company_id"] == "company-a"
            ]
        )
        == 1
    )
    with pytest.raises(AccessDenied):
        control.set_member_role(
            PEER, "company-a", PEER.subject, Role.MEMBER, True, transferred["revision"]
        )
    with pytest.raises(AccessDenied):
        control.set_member_role(
            PEER, "company-a", PEER.subject, Role.COMPANY_OWNER, False, transferred["revision"]
        )


def test_stale_owner_transfer_cannot_create_second_owner(roles):
    _, directory, control, _, _ = roles
    control.set_member_role(OWNER, "company-a", PEER.subject, Role.COMPANY_OWNER, True, "v1")
    with pytest.raises(Conflict):
        control.set_member_role(
            SUPERMAN, "company-a", ADMIN.subject, Role.COMPANY_OWNER, True, "v1"
        )
    assert directory.membership(ADMIN, "company-a").role == Role.COMPANY_ADMIN


def test_company_admin_can_revoke_members_but_not_higher_roles(roles):
    _, directory, control, _, _ = roles
    control.set_member_role(ADMIN, "company-a", PEER.subject, Role.MEMBER, False, "v1")
    assert directory.membership(PEER, "company-a") is None
    for target in (OWNER, ADMIN):
        with pytest.raises(AccessDenied):
            control.set_member_role(
                ADMIN,
                "company-a",
                target.subject,
                Role.MEMBER,
                False,
                directory.company("company-a")["revision"],
            )
    with pytest.raises(AccessDenied):
        control.set_member_role(PEER, "company-a", ADMIN.subject, Role.COMPANY_ADMIN, True, "v1")


def test_invitation_roles_are_checked_before_cognito(roles):
    _, _, control, _, cognito = roles
    with pytest.raises(AccessDenied):
        control.invite(ADMIN, "company-a", "user@example.test", Role.COMPANY_ADMIN, "request-1")
    with pytest.raises(ValueError):
        control.invite(OWNER, "company-a", "user@example.test", Role.COMPANY_OWNER, "request-2")
    cognito.admin_create_user.assert_not_called()
    control.invite(OWNER, "company-a", "user@example.test", Role.COMPANY_ADMIN, "request-3")
    assert cognito.admin_create_user.call_count == 1


def test_owner_can_rename_but_platform_alone_can_suspend(roles):
    _, directory, control, _, _ = roles
    changed = control.update_company(OWNER, "company-a", "New name", True, "v1")
    assert changed["name"] == "New name"
    with pytest.raises(AccessDenied):
        control.update_company(OWNER, "company-a", "New name", False, changed["revision"])
    with pytest.raises(AccessDenied):
        control.update_company(ADMIN, "company-a", "Other", True, changed["revision"])


def test_organization_admin_is_exact_scope_and_cannot_promote_or_touch_company_admins(roles):
    client, directory, _, resources, _ = roles
    client.put(
        membership_item(
            Membership(PEER, "company-a", organization_id="hr", role=Role.ORGANIZATION_ADMIN)
        )
    )
    ordinary = Identity(ISSUER, "ordinary")
    client.put(membership_item(Membership(ordinary, "company-a")))
    service = OrganizationService(
        client, "records", platform=directory, admin_check=directory.require_admin
    )
    original = client.put_item

    def put(**request):
        if request["TableName"] == "records":
            from kirocrew_custom.saas.dynamo import decode_item

            client.put(decode_item(request["Item"]))
            return {}
        return original(**request)

    client.put_item = put
    assert service.permissions(PEER, "company-a", "hr")["manage_members"] is True
    assert service.permissions(PEER, "company-a", "hr")["manage_administrators"] is False
    service.set_member(PEER, "company-a", "hr", ordinary.subject, True, ["hr"], Role.MEMBER)
    for target, role in [
        (ordinary, Role.ORGANIZATION_ADMIN),
        (PEER, Role.MEMBER),
        (OWNER, Role.MEMBER),
        (ADMIN, Role.MEMBER),
    ]:
        with pytest.raises(AccessDenied):
            service.set_member(PEER, "company-a", "hr", target.subject, True, [], role)
    with pytest.raises(AccessDenied):
        service.set_member(PEER, "company-a", "other", ordinary.subject, True, [], Role.MEMBER)
    with pytest.raises(AccessDenied):
        service.create(PEER, "company-a", "child", "Child", "hr")
    with pytest.raises(AccessDenied):
        resources.read(PEER, KEY)
    service.set_member(
        ADMIN, "company-a", "hr", ordinary.subject, True, [], Role.ORGANIZATION_ADMIN
    )
    assert directory.membership(ordinary, "company-a").role == Role.MEMBER
    assert service.permissions(ordinary, "company-a", "hr")["manage_members"] is True
    client.put(membership_item(Membership(ordinary, "company-a", organization_id="hr")))
    assert service.permissions(ordinary, "company-a", "hr")["manage_members"] is False


def test_role_endpoint_uses_verified_identity_and_rejects_platform_role(roles):
    _, _, control, resources, _ = roles
    verifier = Mock()
    verifier.verify.return_value = OWNER
    app = ControlApi(
        control=control,
        verifier=verifier,
        service=resources,
        login=LoginConfig(
            ISSUER,
            "app",
            "https://login.example.test",
            "https://app.example.test/",
            "https://app.example.test/",
        ),
    )
    request = {
        "version": "2.0",
        "requestContext": {"http": {"method": "PUT"}},
        "rawPath": "/companies/company-a/members/peer",
        "headers": {
            "Authorization": "Bearer token",
            "Content-Type": "application/json",
            "If-Match": '"v1"',
        },
        "body": json.dumps({"role": "superman", "active": True}),
    }
    assert app.handle(request)["statusCode"] == 400
    request["body"] = json.dumps({"role": "company_admin", "active": True})
    verifier.verify.return_value = ADMIN
    assert app.handle(request)["statusCode"] == 403
    verifier.verify.return_value = OWNER
    assert app.handle(request)["statusCode"] == 200
    request["rawPath"] = "/companies/company-b/members/peer"
    assert app.handle(request)["statusCode"] == 403


def test_role_change_audit_failure_prevents_transfer_and_records_role_details(roles):
    client, directory, control, _, _ = roles
    client.fail_audit = "authorized_before_access"
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        control.set_member_role(OWNER, "company-a", PEER.subject, Role.COMPANY_ADMIN, True, "v1")
    assert directory.membership(PEER, "company-a").role == Role.MEMBER
    assert client.transactions == 0
    client.fail_audit = None
    control.set_member_role(OWNER, "company-a", PEER.subject, Role.COMPANY_ADMIN, True, "v1")
    assert client.audit[0]["details"] == {
        "subject": "peer",
        "role": "company_admin",
        "previous_role": "member",
        "active": "true",
    }


def test_legacy_owner_bootstrap_requires_superman(roles):
    client, directory, control, _, _ = roles
    legacy = membership_item(Membership(OWNER, "company-a", administrator=True))
    legacy.pop("role")
    client.put(legacy)
    company = directory.company("company-a")
    company.pop("owner")
    client.seed("control", company)
    with pytest.raises(AccessDenied):
        control.set_member_role(OWNER, "company-a", OWNER.subject, Role.COMPANY_OWNER, True, "v1")
    control.set_member_role(SUPERMAN, "company-a", OWNER.subject, Role.COMPANY_OWNER, True, "v1")
    assert directory.membership(OWNER, "company-a").role == Role.COMPANY_OWNER
    assert directory.membership(ADMIN, "company-a").role == Role.COMPANY_ADMIN


def test_owner_transfer_transaction_is_a_valid_sdk_request(roles):
    import boto3
    from botocore.stub import Stubber

    client, _, control, _, _ = roles
    control.set_member_role(OWNER, "company-a", PEER.subject, Role.COMPANY_OWNER, True, "v1")
    request = next(request for kind, request in client.calls if kind == "transaction")
    sdk = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    with Stubber(sdk) as stub:
        stub.add_response("transact_write_items", {}, request)
        sdk.transact_write_items(**request)
        stub.assert_no_pending_responses()
