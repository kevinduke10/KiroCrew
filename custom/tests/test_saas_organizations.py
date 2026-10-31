"""Hierarchy and exact-organization HTTP isolation without network or host state."""

import json
from dataclasses import replace

import boto3
import pytest
from botocore.stub import Stubber
from kirocrew_custom.saas.access import AccessDenied, Action, Grant, Membership, Resource, perform
from kirocrew_custom.saas.directory import list_agents
from kirocrew_custom.saas.dynamo import DynamoRepository, encode_item
from kirocrew_custom.saas.organizations import OrganizationService, organization_key
from kirocrew_custom.saas.records import member_key, membership_item, resource_item, resource_key
from saas_helpers import ADMIN, KEY, NOW, OWNER, PEER
from test_saas_api import api as api_fixture
from test_saas_api import event

api = api_fixture


def request(app, path, payload=None):
    value = event(
        "PUT" if payload is not None else "GET",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    value["rawPath"] = f"/companies/{KEY.company_id}/organizations/{path}"
    return app.handle(value)


def create(app, org, parent=None):
    return request(app, org, {"name": org, "parent_id": parent})


def member(app, org, subject=OWNER.subject, active=True, groups=None):
    return request(
        app, org + "/members", {"subject": subject, "active": active, "groups": groups or []}
    )


def agent(client, organization, resource_id="agent", private=False, grants=None):
    key = replace(KEY, organization_id=organization, resource_id=resource_id)
    client.put(
        resource_item(
            Resource(
                key,
                "agent_definition",
                private,
                OWNER,
                tuple(grants) if grants is not None else (Grant(Action.READ, identity=OWNER),),
            ),
            "v1",
            {"name": resource_id},
        )
    )
    return key


def test_nested_creation_and_membership_roundtrip(api):
    app, client, verifier = api
    verifier.verify.return_value = ADMIN
    for organization, parent in [("root", None), ("child", "root"), ("grandchild", "child")]:
        assert create(app, organization, parent)["statusCode"] == 201
    assert create(app, "child", "root")["statusCode"] == 409
    assert (
        client.items[tuple(organization_key(KEY.company_id, "child").values())]["parent_id"]
        == "root"
    )
    key = agent(client, "grandchild")
    assert member(app, "grandchild")["statusCode"] == 200
    verifier.verify.return_value = OWNER
    assert app.handle(scoped_event(key))["statusCode"] == 200
    assert request(app, "grandchild/agents")["statusCode"] == 200
    verifier.verify.return_value = ADMIN
    assert member(app, "grandchild", active=False)["statusCode"] == 200
    verifier.verify.return_value = OWNER
    assert app.handle(scoped_event(key))["statusCode"] == 403
    assert request(app, "grandchild/agents")["statusCode"] == 403


def scoped_event(key):
    value = event(company=key.company_id)
    value["rawPath"] = (
        f"/companies/{key.company_id}/organizations/{key.organization_id}"
        f"/spaces/{key.space_id}/resources/{key.resource_id}"
    )
    return value


@pytest.mark.parametrize("identity", [OWNER, PEER, replace(ADMIN, subject="outsider")])
def test_non_admin_cannot_create_or_assign_membership(api, identity):
    app, client, verifier = api
    verifier.verify.return_value = identity
    assert create(app, "root")["statusCode"] == 403
    assert member(app, "root")["statusCode"] == 403
    assert not any(kind == "put" for kind, _ in client.calls)


def test_invalid_parents_and_cycles_are_rejected(api):
    app, client, verifier = api
    verifier.verify.return_value = ADMIN
    assert create(app, "root", "root")["statusCode"] == 400
    assert create(app, "child", "missing")["statusCode"] == 403
    client.put(
        {
            **organization_key("company-b", "foreign"),
            "type": "organization",
            "schema": 1,
            "company_id": "company-b",
            "organization_id": "foreign",
            "name": "foreign",
            "parent_id": None,
        }
    )
    assert create(app, "child", "foreign")["statusCode"] == 403
    assert create(app, "root")["statusCode"] == 201
    client.items[tuple(organization_key(KEY.company_id, "root").values())]["parent_id"] = "root"
    assert create(app, "child", "root")["statusCode"] == 400


@pytest.mark.parametrize("organization", ["root", "child", "grandchild", "sibling", "unrelated"])
def test_ancestry_does_not_grant_visibility_even_to_owner_or_admin(api, organization):
    app, client, verifier = api
    verifier.verify.return_value = ADMIN
    for org, parent in [
        ("root", None),
        ("child", "root"),
        ("hr", "child"),
        ("grandchild", "hr"),
        ("sibling", "root"),
        ("unrelated", None),
    ]:
        assert create(app, org, parent)["statusCode"] == 201
    key = agent(client, organization, private=True)
    for identity in (OWNER, ADMIN):
        verifier.verify.return_value = identity
        assert app.handle(scoped_event(key))["statusCode"] == 403
        assert request(app, organization + "/agents")["statusCode"] == 403


def test_directory_preserves_private_grants_and_does_not_disclose_other_organizations(api):
    app, client, verifier = api
    agent(client, "hr", "visible")
    agent(client, "hr", "hidden", grants=[])
    agent(client, "hr", "private", private=True)
    agent(client, "finance", "finance-secret")
    result = request(app, "hr/agents")
    assert result["statusCode"] == 200
    assert {row["resource_id"] for row in json.loads(result["body"])["agents"]} == {
        "visible",
        "private",
    }
    assert "hidden" not in result["body"] and "finance-secret" not in result["body"]
    verifier.verify.return_value = ADMIN
    assert json.loads(request(app, "hr/agents")["body"]) == {"agents": []}
    assert not client.audit


def test_company_group_does_not_authorize_organization_group(api):
    app, client, _ = api
    key = agent(client, "hr", grants=[Grant(Action.READ, group="shared")])
    client.put(membership_item(Membership(OWNER, KEY.company_id, frozenset({"shared"}))))
    assert app.handle(scoped_event(key))["statusCode"] == 403
    client.put(
        membership_item(
            Membership(OWNER, KEY.company_id, frozenset({"shared"}), organization_id="hr")
        )
    )
    assert app.handle(scoped_event(key))["statusCode"] == 200
    client.put(
        membership_item(
            Membership(OWNER, KEY.company_id, frozenset({"shared"}), organization_id="finance")
        )
    )
    client.items[tuple(member_key(KEY.company_id, OWNER, "hr").values())]["active"] = False
    assert app.handle(scoped_event(key))["statusCode"] == 403


def test_unscoped_routes_and_legacy_records_do_not_bypass_boundary(api):
    app, client, _ = api
    value = event()
    value["rawPath"] = (
        f"/companies/{KEY.company_id}/spaces/{KEY.space_id}/resources/{KEY.resource_id}"
    )
    assert app.handle(value)["statusCode"] == 404
    client.items[tuple(resource_key(KEY).values())].pop("organization_id")
    assert app.handle(event())["statusCode"] == 503
    with pytest.raises(ValueError):
        resource_key(replace(KEY, organization_id=""))


def test_member_admin_injection_and_unknown_company_member_are_rejected(api):
    app, client, verifier = api
    verifier.verify.return_value = ADMIN
    assert create(app, "root")["statusCode"] == 201
    assert member(app, "root", subject="unknown")["statusCode"] == 403
    response = request(
        app,
        "root/members",
        {"subject": OWNER.subject, "active": True, "groups": [], "administrator": True},
    )
    assert response["statusCode"] == 400
    assert member(app, "root")["statusCode"] == 200
    assert not client.items[tuple(member_key(KEY.company_id, OWNER, "root").values())][
        "administrator"
    ]
    assert not client.items[tuple(member_key(KEY.company_id, OWNER).values())]["administrator"]


def test_directory_paginates_without_returning_hidden_ids(api):
    app, client, _ = api
    for index in range(55):
        agent(client, "hr", f"hidden-{index:03}", grants=[])
    agent(client, "hr", "visible-last")
    response = request(app, "hr/agents")
    assert response["statusCode"] == 200
    assert [row["resource_id"] for row in json.loads(response["body"])["agents"]] == [
        "visible-last"
    ]
    assert len([kind for kind, _ in client.calls if kind == "query"]) == 2


def test_run_permission_still_requires_exact_organization(api):
    app, client, _ = api
    key = agent(client, "finance", grants=[Grant(Action.RUN, identity=OWNER)])
    kwargs = dict(
        identity=OWNER,
        key=key,
        action=Action.RUN,
        audit=app.service.audit,
        clock=lambda: NOW,
        operation_id="run",
        operation=lambda resource: resource.key.organization_id,
    )
    with pytest.raises(AccessDenied):
        perform(repository=DynamoRepository(client, "records"), **kwargs)
    client.put(membership_item(Membership(OWNER, KEY.company_id, organization_id="finance")))
    assert perform(repository=DynamoRepository(client, "records"), **kwargs) == "finance"


def test_real_sdk_validates_creation_membership_and_query(api):
    app, _, _ = api
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    company_member = membership_item(Membership(ADMIN, KEY.company_id, administrator=True))
    org_member = membership_item(Membership(ADMIN, KEY.company_id, organization_id="root"))
    with Stubber(client) as stub:
        for _ in range(2):
            stub.add_response("get_item", {"Item": encode_item(company_member)})
        stub.add_response("put_item", {})
        assert OrganizationService(client, "records").create(
            ADMIN, KEY.company_id, "root", "Root", None
        )
        for item in (company_member, org_member):
            stub.add_response("get_item", {"Item": encode_item(item)})
        stub.add_response("query", {"Items": []})
        for item in (company_member, org_member):
            stub.add_response("get_item", {"Item": encode_item(item)})
        app.service.client = client
        assert list_agents(app.service, ADMIN, KEY.company_id, "root") == {"agents": []}
        stub.assert_no_pending_responses()


def test_directory_failure_is_bounded_and_never_returns_partial_results(api, monkeypatch):
    app, client, _ = api
    from kirocrew_custom.saas import directory

    monkeypatch.setattr(directory, "MAX_DIRECTORY_PAGES", 1)
    for index in range(51):
        agent(client, "hr", f"agent-{index:03}")
    response = request(app, "hr/agents")
    assert response["statusCode"] == 503
    assert "agent-" not in response["body"]
    assert len([kind for kind, _ in client.calls if kind == "query"]) == 1


def test_company_revocation_and_foreign_company_deny_directory(api):
    app, client, _ = api
    key = agent(client, "hr")
    client.items[tuple(member_key(KEY.company_id, OWNER).values())]["active"] = False
    assert request(app, "hr/agents")["statusCode"] == 403
    assert app.handle(scoped_event(key))["statusCode"] == 403
    foreign = replace(key, company_id="company-b")
    assert app.handle(scoped_event(foreign))["statusCode"] == 403


def test_organization_admin_flag_cannot_promote_company_member(api):
    with pytest.raises(ValueError):
        Membership(OWNER, KEY.company_id, administrator=True, organization_id="hr")


def test_mutation_methods_and_unexpected_fields_are_rejected(api):
    app, _, verifier = api
    verifier.verify.return_value = ADMIN
    value = event(
        "POST", headers={"Content-Type": "application/json"}, body={"name": "x", "parent_id": None}
    )
    value["rawPath"] = f"/companies/{KEY.company_id}/organizations/x"
    assert app.handle(value)["statusCode"] == 405
    assert (
        request(app, "x", {"name": "x", "parent_id": None, "company_id": "other"})["statusCode"]
        == 400
    )


def test_hierarchy_depth_is_bounded(api, monkeypatch):
    app, _, verifier = api
    from kirocrew_custom.saas import organizations

    verifier.verify.return_value = ADMIN
    monkeypatch.setattr(organizations, "MAX_ORGANIZATION_DEPTH", 2)
    assert create(app, "root")["statusCode"] == 201
    assert create(app, "child", "root")["statusCode"] == 201
    assert create(app, "grandchild", "child")["statusCode"] == 400


def test_membership_change_during_listing_prevents_delivery(api, monkeypatch):
    app, client, _ = api
    agent(client, "hr")
    original_query = client.query

    def revoke(**kwargs):
        result = original_query(**kwargs)
        client.items[tuple(member_key(KEY.company_id, OWNER, "hr").values())]["active"] = False
        return result

    monkeypatch.setattr(client, "query", revoke)
    response = request(app, "hr/agents")
    assert response["statusCode"] == 403
    assert "agents" not in json.loads(response["body"])


def test_scoped_member_read_and_write_are_valid_sdk_requests(api):
    app, _, _ = api
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    admin = membership_item(Membership(ADMIN, KEY.company_id, administrator=True))
    target = membership_item(Membership(OWNER, KEY.company_id))
    organization = {
        **organization_key(KEY.company_id, "hr"),
        "schema": 1,
        "type": "organization",
        "company_id": KEY.company_id,
        "organization_id": "hr",
        "parent_id": None,
        "name": "HR",
    }
    with Stubber(client) as stub:
        for item in (
            admin,
            membership_item(Membership(ADMIN, KEY.company_id, organization_id="hr")),
            admin,
            organization,
            target,
            {},
            admin,
        ):
            stub.add_response("get_item", {"Item": encode_item(item)})
        assigned = membership_item(
            Membership(OWNER, KEY.company_id, frozenset({"hr"}), organization_id="hr")
        )
        assigned["updated_by"] = {"issuer": ADMIN.issuer, "subject": ADMIN.subject}
        stub.add_response("put_item", {}, {"TableName": "records", "Item": encode_item(assigned)})
        result = OrganizationService(client, "records").set_member(
            ADMIN, KEY.company_id, "hr", OWNER.subject, True, ["hr"]
        )
        assert result["active"] is True
        stub.assert_no_pending_responses()
