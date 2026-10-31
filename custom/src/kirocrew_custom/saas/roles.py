"""Application role catalog; platform Superman grants remain separate."""

from enum import Enum


class Role(str, Enum):
    COMPANY_OWNER = "company_owner"
    COMPANY_ADMIN = "company_admin"
    ORGANIZATION_ADMIN = "organization_admin"
    MEMBER = "member"


COMPANY_ADMIN_ROLES = frozenset({Role.COMPANY_OWNER, Role.COMPANY_ADMIN})
COMPANY_ROLES = COMPANY_ADMIN_ROLES | {Role.MEMBER}
ORGANIZATION_ROLES = frozenset({Role.ORGANIZATION_ADMIN, Role.MEMBER})


def normalized_role(role: Role | None, administrator: bool, organization: str) -> Role:
    if type(administrator) is not bool:
        raise ValueError("Invalid administrator flag")
    if role is None:
        role = Role.COMPANY_ADMIN if administrator and not organization else Role.MEMBER
    if not isinstance(role, Role) or role not in (
        ORGANIZATION_ROLES if organization else COMPANY_ROLES
    ):
        raise ValueError("Invalid role scope")
    if organization and administrator:
        raise ValueError("Organization membership cannot grant company administration")
    return role
