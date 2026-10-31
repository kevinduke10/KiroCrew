"""Browser asset allowlist, packaging and template permission boundaries."""

from kirocrew_custom.saas.web_assets import asset_response
from test_saas_template import template


def test_browser_assets_are_self_contained_and_no_paths_escape_allowlist():
    for path in ("/", "/app.mjs", "/oauth.mjs", "/i18n.mjs", "/style.css"):
        response = asset_response(
            {"rawPath": path, "requestContext": {"http": {"method": "GET"}}},
            "https://login.example.test",
        )
        assert response["statusCode"] == 200
        assert response["body"]
        assert "frame-ancestors 'none'" in response["headers"]["content-security-policy"]
    assert (
        asset_response(
            {"rawPath": "/../bootstrap.py", "requestContext": {"http": {"method": "GET"}}},
            "https://login.example.test",
        )
        is None
    )


def test_neither_lambda_can_grant_superman_and_control_permissions_are_separate():
    resources = template()["Resources"]
    for name in ("PooledApiFunction", "ControlApiFunction"):
        statements = resources[name]["Properties"]["Policies"][0]["Statement"]
        role_grants = [
            s
            for s in statements
            if {"GetAtt": "PlatformRolesTable.Arn"} == s["Resource"]
            or (
                isinstance(s["Resource"], list)
                and {"GetAtt": "PlatformRolesTable.Arn"} in s["Resource"]
            )
        ]
        assert role_grants
        assert all(s["Action"] == ["dynamodb:GetItem"] for s in role_grants)
        audit_grants = [s for s in statements if s["Resource"] == {"GetAtt": "AuditTable.Arn"}]
        assert all(s["Action"] == ["dynamodb:PutItem"] for s in audit_grants)
    main = resources["PooledApiFunction"]["Properties"]["Policies"][0]["Statement"]
    assert not any(any(action.startswith("cognito-idp:") for action in s["Action"]) for s in main)
    assert (
        resources["ControlApiFunction"]["Properties"]["Environment"]["Variables"]["CONTROL_PLANE"]
        == "true"
    )
