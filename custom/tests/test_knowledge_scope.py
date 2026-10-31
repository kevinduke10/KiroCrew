"""Knowledge is company-wide, organization-local, or shared down selected subtrees."""

from dataclasses import replace

import pytest
from kirocrew_custom.saas.access import AccessDenied, Action, Grant, Membership, Resource
from kirocrew_custom.saas.organizations import organization_key
from kirocrew_custom.saas.records import Conflict, member_key, membership_item, resource_item
from knowledge_helpers import HR_ADMIN, setup_knowledge
from saas_helpers import ADMIN, KEY, OWNER


@pytest.fixture
def knowledge():
    return setup_knowledge()


def upload(service, doc, organization=None, children=None, actor=ADMIN):
    return service.upload(
        actor,
        KEY.company_id,
        doc,
        organization=organization,
        title=doc,
        extension="txt",
        data=(doc + " policy").encode(),
        shared_children=children or [],
    )


@pytest.mark.parametrize(
    "organization,expected",
    [
        ("root", {"company"}),
        ("hr", {"company", "hr-local", "hr-shared"}),
        ("recruiting", {"company", "hr-shared", "recruiting-local"}),
        ("interns", {"company", "hr-shared"}),
        ("benefits", {"company"}),
        ("finance", {"company"}),
    ],
)
def test_company_own_and_selected_child_subtree_are_combined(knowledge, organization, expected):
    _, cloud, service, _, _ = knowledge
    upload(service, "company")
    upload(service, "hr-local", "hr")
    upload(service, "hr-shared", "hr", ["recruiting"])
    upload(service, "recruiting-local", "recruiting")
    result = service.search(OWNER, KEY.company_id, organization, "policy")
    assert {item["document_id"] for item in result["sources"]} == expected
    filters = cloud.requests[-1]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"][
        "andAll"
    ]
    assert filters[0] == {"equals": {"key": "company_id", "value": KEY.company_id}}
    assert set(filters[1]["in"]["value"]) == expected


@pytest.mark.parametrize("children", [["root"], ["finance"], ["interns"], ["hr"], ["missing"]])
def test_only_existing_direct_children_can_be_share_roots(knowledge, children):
    _, cloud, service, _, _ = knowledge
    with pytest.raises(AccessDenied):
        upload(service, "bad-share", "hr", children)
    assert not cloud.objects


def test_organization_admin_cannot_publish_company_or_sibling_data(knowledge):
    _, _, service, _, _ = knowledge
    assert upload(service, "own", "hr", ["recruiting"], HR_ADMIN)
    for organization in (None, "finance", "recruiting"):
        with pytest.raises(AccessDenied):
            upload(service, "forbidden", organization, actor=HR_ADMIN)
    with pytest.raises(AccessDenied):
        upload(service, "member", "hr", actor=OWNER)


def test_receiving_child_cannot_reshare_parent_document(knowledge):
    client, _, service, _, _ = knowledge
    from kirocrew_custom.saas.roles import Role

    client.put(
        membership_item(
            Membership(
                OWNER, KEY.company_id, organization_id="recruiting", role=Role.ORGANIZATION_ADMIN
            )
        )
    )
    document = upload(service, "parent", "hr", ["recruiting"])
    with pytest.raises(AccessDenied):
        service.sharing(OWNER, KEY.company_id, "parent", ["benefits"], True, document["revision"])


def test_revoked_share_blocks_stale_vector_content_without_reindex(knowledge):
    _, cloud, service, _, _ = knowledge
    doc = upload(service, "hr-shared", "hr", ["recruiting"])
    assert service.search(OWNER, KEY.company_id, "interns", "policy")["sources"]
    service.sharing(HR_ADMIN, KEY.company_id, "hr-shared", [], True, doc["revision"])
    assert cloud.results
    assert service.search(OWNER, KEY.company_id, "interns", "policy")["sources"] == []
    assert service.search(OWNER, KEY.company_id, "hr", "policy")["sources"]


def test_mid_query_share_revocation_and_membership_revocation_prevent_delivery(knowledge):
    client, cloud, service, _, _ = knowledge
    doc = upload(service, "shared", "hr", ["recruiting"])
    cloud.before_retrieve = lambda: service.sharing(
        HR_ADMIN, KEY.company_id, "shared", [], True, doc["revision"]
    )
    assert service.search(OWNER, KEY.company_id, "interns", "policy")["sources"] == []
    cloud.before_retrieve = None
    upload(service, "company")

    def revoke():
        client.items[tuple(member_key(KEY.company_id, OWNER, "interns").values())]["active"] = False

    cloud.before_retrieve = revoke
    with pytest.raises(AccessDenied):
        service.search(OWNER, KEY.company_id, "interns", "policy")


@pytest.mark.parametrize("mutation", ["company", "revision", "location", "document"])
def test_forged_or_stale_index_results_never_reach_context(knowledge, mutation):
    _, cloud, service, _, _ = knowledge
    upload(service, "company")
    result = cloud.results[0]
    if mutation == "location":
        result["location"]["s3Location"]["uri"] = "s3://foreign/private.txt"
    else:
        field = {"company": "company_id", "revision": "source_version", "document": "document_id"}[
            mutation
        ]
        result["metadata"][field] = "foreign"
    assert service.search(OWNER, KEY.company_id, "hr", "policy")["sources"] == []


def test_agent_run_grants_and_visibility_are_independent_of_knowledge_shares(knowledge):
    client, _, service, _, _ = knowledge
    upload(service, "shared", "hr", ["recruiting"])
    key = replace(KEY, organization_id="interns", resource_id="agent")
    client.put(resource_item(Resource(key, "agent_definition", False, OWNER, ()), "v1", {}))
    with pytest.raises(AccessDenied):
        service.for_agent(OWNER, key, "policy")
    client.put(
        resource_item(
            Resource(key, "agent_definition", False, OWNER, (Grant(Action.RUN, identity=OWNER),)),
            "v2",
            {},
        )
    )
    assert service.for_agent(OWNER, key, "policy")["sources"][0]["document_id"] == "shared"
    client.items[tuple(member_key(KEY.company_id, OWNER, "interns").values())]["active"] = False
    with pytest.raises(AccessDenied):
        service.for_agent(OWNER, key, "policy")


def test_retry_preserves_identity_and_does_not_restore_revoked_sharing(knowledge):
    _, cloud, service, _, _ = knowledge
    cloud.fail_ingest = True
    with pytest.raises(RuntimeError):
        upload(service, "retry", "hr", ["recruiting"])
    cloud.fail_ingest = False
    result = upload(service, "retry", "hr", ["recruiting"])
    updated = service.sharing(HR_ADMIN, KEY.company_id, "retry", [], True, result["revision"])
    retried = upload(service, "retry", "hr", ["recruiting"])
    assert retried["shared_children"] == ()
    assert retried["revision"] == updated["revision"]
    assert len(cloud.objects) == 2
    with pytest.raises(Conflict):
        upload(service, "retry", "finance")
    with pytest.raises(Conflict):
        service.sharing(HR_ADMIN, KEY.company_id, "retry", [], True, result["revision"])


def test_disable_source_and_audit_fail_closed(knowledge):
    _, _, service, _, _ = knowledge
    result = upload(service, "company")
    service.sharing(ADMIN, KEY.company_id, "company", [], False, result["revision"])
    assert service.search(OWNER, KEY.company_id, "hr", "policy")["sources"] == []
    with pytest.raises(Conflict):
        upload(service, "company")
    service.resources.audit.client.fail_audit = "authorized_before_access"
    with pytest.raises(Exception):
        upload(service, "blocked")
    assert service.store.get(KEY.company_id, "blocked") is None


def test_unknown_or_other_company_is_denied_before_vector_search(knowledge):
    _, cloud, service, _, _ = knowledge
    upload(service, "company")
    with pytest.raises(AccessDenied):
        service.search(OWNER, "company-b", "hr", "policy")
    with pytest.raises(AccessDenied):
        service.search(OWNER, KEY.company_id, "nonmember", "policy")
    assert not cloud.requests


def test_corrupted_parent_cycle_denies_publishing_and_retrieval(knowledge):
    client, cloud, service, _, _ = knowledge
    client.items[tuple(organization_key(KEY.company_id, "root").values())]["parent_id"] = "hr"
    with pytest.raises(AccessDenied):
        upload(service, "bad", "hr", ["recruiting"])
    with pytest.raises(AccessDenied):
        service.search(OWNER, KEY.company_id, "hr", "policy")
    assert not cloud.objects and not cloud.requests


def test_agent_endpoint_rejects_non_agent_resource_with_run_grant(knowledge):
    client, cloud, service, _, _ = knowledge
    client.put(
        resource_item(
            Resource(KEY, "memory", False, OWNER, (Grant(Action.RUN, identity=OWNER),)), "v1", {}
        )
    )
    with pytest.raises(AccessDenied):
        service.for_agent(OWNER, KEY, "policy")
    assert not cloud.requests
