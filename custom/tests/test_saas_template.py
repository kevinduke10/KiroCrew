"""Pin audit and metadata-writing IAM boundaries in the deployment source."""

from pathlib import Path

import yaml


class TemplateLoader(yaml.SafeLoader):
    pass


def _intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {tag: value}


TemplateLoader.add_multi_constructor("!", _intrinsic)


def template():
    path = Path(__file__).resolve().parents[1] / "deploy/pooled-api.yaml"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=TemplateLoader)


def test_api_role_can_append_audit_but_cannot_change_membership_or_acl():
    resources = template()["Resources"]
    statements = resources["PooledApiFunction"]["Properties"]["Policies"][0]["Statement"]
    audit = [
        statement
        for statement in statements
        if statement["Resource"] == {"GetAtt": "AuditTable.Arn"}
    ]
    assert len(audit) == 1
    assert audit[0]["Action"] == ["dynamodb:PutItem"]
    updates = [
        statement for statement in statements if "dynamodb:UpdateItem" in statement["Action"]
    ]
    assert len(updates) == 1
    attributes = updates[0]["Condition"]["ForAllValues:StringEquals"]["dynamodb:Attributes"]
    assert set(attributes) == {"pk", "sk", "content", "revision"}
    assert updates[0]["Condition"]["Null"]["dynamodb:Attributes"] == "false"
    assert not any("dynamodb:DeleteItem" in statement["Action"] for statement in statements)
    for table in ("RecordsTable", "AuditTable"):
        assert resources[table]["DeletionPolicy"] == "Retain"
        assert resources[table]["Properties"]["SSESpecification"]["SSEEnabled"] is True
        assert (
            resources[table]["Properties"]["PointInTimeRecoverySpecification"][
                "PointInTimeRecoveryEnabled"
            ]
            is True
        )
    assert "TimeToLiveSpecification" not in resources["AuditTable"]["Properties"]


def test_managed_login_is_invitation_only_code_flow_and_api_has_scoped_authorizer():
    resources = template()["Resources"]
    assert (
        resources["UserPool"]["Properties"]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"]
        is True
    )
    client = resources["UserPoolClient"]["Properties"]
    assert client["AllowedOAuthFlows"] == ["code"]
    assert client["GenerateSecret"] is False
    assert client["EnableTokenRevocation"] is True
    auth = resources["PooledHttpApi"]["Properties"]["Auth"]
    assert auth["DefaultAuthorizer"] == "CognitoAccess"
    assert auth["Authorizers"]["CognitoAccess"]["AuthorizationScopes"] == [
        "aws.cognito.signin.user.admin"
    ]
    assert "VpcConfig" not in resources["PooledApiFunction"]["Properties"]
    assert "FunctionUrlConfig" not in resources["PooledApiFunction"]["Properties"]


def test_organization_writes_cannot_replace_company_memberships_or_resources():
    properties = template()["Resources"]["PooledApiFunction"]["Properties"]
    statements = properties["Policies"][0]["Statement"]
    writes = [
        s
        for s in statements
        if "dynamodb:PutItem" in s["Action"] and s["Resource"] == {"GetAtt": "RecordsTable.Arn"}
    ]
    assert len(writes) == 1
    assert writes[0]["Condition"]["ForAllValues:StringLike"] == {"dynamodb:LeadingKeys": ["ORG#*"]}
    assert writes[0]["Condition"]["Null"]["dynamodb:LeadingKeys"] == "false"
    routes = {value["Properties"]["Path"] for value in properties["Events"].values()}
    prefix = "/companies/{company}/organizations/{organization}"
    assert {
        prefix,
        prefix + "/members",
        prefix + "/agents",
        prefix + "/spaces/{space}/resources/{resource}",
    } <= routes
    assert "/companies/{company}/spaces/{space}/resources/{resource}" not in routes
