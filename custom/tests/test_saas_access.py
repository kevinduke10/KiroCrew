"""Offline authorization matrix; no production identity or persistence adapters."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from kirocrew_custom.saas.access import (
    AccessDenied,
    Action,
    AuditEvent,
    Grant,
    Identity,
    Membership,
    Resource,
    ResourceKey,
    perform,
)

OWNER = Identity("verified-issuer", "owner")
PEER = Identity("verified-issuer", "peer")
ADMIN = Identity("verified-issuer", "admin")
KEY = ResourceKey("company-a", "private-space", "conversation", "hr")
NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)


@pytest.fixture
def boundary():
    repository = Mock()
    repository.membership.return_value = Membership(OWNER, KEY.company_id)

    def membership(identity, company, organization=""):
        member = repository.membership.return_value
        return (
            (
                Membership(
                    member.identity, member.company_id, member.groups, organization_id=organization
                )
                if organization
                else member
            )
            if member is not None
            else None
        )

    repository.membership.side_effect = membership
    repository.resource.return_value = Resource(KEY, "conversation", True, OWNER)
    audit = Mock()
    audit.record_access.return_value = "persisted-receipt"
    operation = Mock(return_value="content")
    arguments = dict(
        identity=OWNER,
        key=KEY,
        action=Action.READ,
        repository=repository,
        audit=audit,
        operation_id="operation-1",
        clock=lambda: NOW,
        operation=operation,
    )
    return arguments


def test_private_owner_reads_authoritative_resource_without_privilege_audit(boundary):
    assert perform(**boundary) == "content"
    boundary["operation"].assert_called_once_with(boundary["repository"].resource.return_value)
    boundary["audit"].record_access.assert_not_called()


@pytest.mark.parametrize("administrator", [False, True])
@pytest.mark.parametrize("failure", ["missing", "other_company", "other_identity", "other_space"])
def test_scope_or_membership_mismatch_never_reaches_content(boundary, administrator, failure):
    repository = boundary["repository"]
    member = Membership(OWNER, KEY.company_id, administrator=administrator)
    if failure == "missing":
        member = None
    elif failure == "other_company":
        member = replace(member, company_id="company-b")
    elif failure == "other_identity":
        member = replace(member, identity=PEER)
    else:
        repository.resource.return_value = replace(
            repository.resource.return_value, key=replace(KEY, space_id="other-space")
        )
    repository.membership.return_value = member
    with pytest.raises(AccessDenied):
        perform(**boundary)
    boundary["operation"].assert_not_called()
    boundary["audit"].record_access.assert_not_called()


def test_coworker_grants_cannot_override_private_space(boundary):
    boundary["identity"] = PEER
    repository = boundary["repository"]
    repository.membership.return_value = Membership(PEER, KEY.company_id)
    repository.resource.return_value = replace(
        repository.resource.return_value, grants=(Grant(Action.READ, identity=PEER),)
    )
    with pytest.raises(AccessDenied):
        perform(**boundary)
    boundary["operation"].assert_not_called()


def test_admin_audit_is_confirmed_before_content_and_contains_exact_scope(boundary):
    boundary["identity"] = ADMIN
    boundary["repository"].membership.return_value = Membership(
        ADMIN, KEY.company_id, administrator=True
    )
    order = []

    def persist(event):
        assert event == AuditEvent("operation-1", ADMIN, KEY, Action.READ, NOW)
        order.append("persisted")
        return "receipt"

    def read(resource):
        assert order == ["persisted"]
        assert resource.key == KEY
        order.append("read")
        return "content"

    boundary["audit"].record_access.side_effect = persist
    boundary["operation"] = read
    assert perform(**boundary) == "content"
    assert order == ["persisted", "read"]


@pytest.mark.parametrize("failure", ["exception", "empty_receipt", "missing_id", "naive_clock"])
def test_admin_audit_failure_prevents_disclosure(boundary, failure):
    boundary["identity"] = ADMIN
    boundary["repository"].membership.return_value = Membership(
        ADMIN, KEY.company_id, administrator=True
    )
    if failure == "exception":
        boundary["audit"].record_access.side_effect = OSError("audit unavailable")
    elif failure == "empty_receipt":
        boundary["audit"].record_access.return_value = ""
    elif failure == "missing_id":
        boundary["operation_id"] = ""
    else:
        boundary["clock"] = lambda: NOW.replace(tzinfo=None)
    with pytest.raises((AccessDenied, OSError)):
        perform(**boundary)
    boundary["operation"].assert_not_called()


@pytest.mark.parametrize("action", [Action.EDIT, Action.RUN, Action.MANAGE])
def test_admin_exception_is_read_only(boundary, action):
    boundary["identity"] = ADMIN
    boundary["action"] = action
    boundary["repository"].membership.return_value = Membership(
        ADMIN, KEY.company_id, administrator=True
    )
    with pytest.raises(AccessDenied):
        perform(**boundary)
    boundary["operation"].assert_not_called()


@pytest.mark.parametrize("group_grant", [False, True])
def test_shared_agent_run_does_not_grant_edit_or_conversation_read(boundary, group_grant):
    repository = boundary["repository"]
    repository.membership.return_value = Membership(OWNER, KEY.company_id, frozenset({"hr"}))
    grant = Grant(Action.RUN, group="hr") if group_grant else Grant(Action.RUN, identity=OWNER)
    repository.resource.return_value = Resource(KEY, "agent_definition", False, OWNER, (grant,))
    boundary["action"] = Action.RUN
    assert perform(**boundary) == "content"
    boundary["operation"].reset_mock()
    for action in (Action.READ, Action.EDIT, Action.MANAGE):
        boundary["action"] = action
        with pytest.raises(AccessDenied):
            perform(**boundary)
    boundary["action"] = Action.RUN
    repository.resource.return_value = replace(
        repository.resource.return_value, kind="conversation"
    )
    with pytest.raises(AccessDenied):
        perform(**boundary)
    boundary["operation"].assert_not_called()


@pytest.mark.parametrize("revoke", ["company", "group", "grant"])
def test_next_operation_rechecks_revoked_membership_and_grants(boundary, revoke):
    repository = boundary["repository"]
    repository.membership.return_value = Membership(OWNER, KEY.company_id, frozenset({"hr"}))
    repository.resource.return_value = Resource(
        KEY, "conversation", False, OWNER, (Grant(Action.READ, group="hr"),)
    )
    assert perform(**boundary) == "content"
    boundary["operation"].reset_mock()
    if revoke == "company":
        repository.membership.return_value = None
    elif revoke == "group":
        repository.membership.return_value = Membership(OWNER, KEY.company_id)
    else:
        repository.resource.return_value = replace(repository.resource.return_value, grants=())
    with pytest.raises(AccessDenied):
        perform(**boundary)
    boundary["operation"].assert_not_called()


def test_same_subject_from_other_issuer_does_not_inherit_owner_access(boundary):
    identity = Identity("other-issuer", OWNER.subject)
    boundary["identity"] = identity
    boundary["repository"].membership.return_value = Membership(identity, KEY.company_id)
    with pytest.raises(AccessDenied):
        perform(**boundary)
    boundary["operation"].assert_not_called()
