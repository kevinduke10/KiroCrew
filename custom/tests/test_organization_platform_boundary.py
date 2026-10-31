"""Organization discovery keeps customer scope alongside audited platform support."""

import pytest
from kirocrew_custom.saas.access import AccessDenied
from kirocrew_custom.saas.directory import list_agents
from kirocrew_custom.saas.organizations import OrganizationService
from platform_helpers import SUPERMAN
from saas_helpers import ADMIN, KEY, OWNER
from test_platform_administration import platform as platform_fixture

platform = platform_fixture


def test_platform_support_does_not_become_ordinary_agent_directory_membership(platform):
    _, _, _, resources, _ = platform
    with pytest.raises(AccessDenied):
        list_agents(resources, SUPERMAN, KEY.company_id, KEY.organization_id)


def test_suspended_company_blocks_organization_management_and_discovery(platform):
    _, directory, control, resources, _ = platform
    control.update_company(SUPERMAN, KEY.company_id, "Paused", False, "v1")
    organizations = OrganizationService(resources.client, resources.records_table, directory)
    with pytest.raises(AccessDenied):
        organizations.create(ADMIN, KEY.company_id, "new", "New", None)
    with pytest.raises(AccessDenied):
        list_agents(resources, OWNER, KEY.company_id, KEY.organization_id)
