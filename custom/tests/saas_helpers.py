"""Deterministic in-memory transport for the real DynamoDB adapter, tests only."""

from copy import deepcopy
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from kirocrew_custom.saas.access import Identity, Membership, Resource, ResourceKey
from kirocrew_custom.saas.dynamo import decode_item, encode_item
from kirocrew_custom.saas.records import membership_item, resource_item

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool"
OWNER = Identity(ISSUER, "owner")
ADMIN = Identity(ISSUER, "admin")
PEER = Identity(ISSUER, "peer")
KEY = ResourceKey("company-a", "private-space", "conversation", "hr")


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "sensitive upstream text"}}, "Test")


class MemoryDynamo:
    def __init__(self):
        self.items = {}
        self.audit = []
        self.calls = []
        self.fail_audit = None
        self.fail_content = False
        self.before_content = None
        self.put(membership_item(Membership(OWNER, KEY.company_id)))
        self.put(membership_item(Membership(ADMIN, KEY.company_id, administrator=True)))
        self.put(membership_item(Membership(PEER, KEY.company_id)))
        for person in (OWNER, ADMIN, PEER):
            self.put(
                membership_item(
                    Membership(person, KEY.company_id, organization_id=KEY.organization_id)
                )
            )
        self.put(
            resource_item(Resource(KEY, "conversation", True, OWNER), "v1", {"text": "secret"})
        )

    def put(self, item):
        self.items[item["pk"], item["sk"]] = deepcopy(item)

    def get_item(self, **request):
        self.calls.append(("get", deepcopy(request)))
        assert request["ConsistentRead"] is True
        key = decode_item(request["Key"])
        if key["sk"].startswith("RESOURCE#") and "ProjectionExpression" not in request:
            if self.before_content:
                self.before_content()
            if self.fail_content:
                raise client_error("InternalServerError")
        item = self.items.get((key["pk"], key["sk"]))
        if item is None:
            return {}
        if "ProjectionExpression" in request:
            fields = request["ExpressionAttributeNames"].values()
            item = {k: v for k, v in item.items() if k in fields}
        return {"Item": encode_item(deepcopy(item))}

    def update_item(self, **request):
        self.calls.append(("update", deepcopy(request)))
        key = decode_item(request["Key"])
        values = decode_item(request["ExpressionAttributeValues"])
        item = self.items.get((key["pk"], key["sk"]))
        if item is None or item["revision"] != values[":expected"]:
            raise client_error("ConditionalCheckFailedException")
        item.update(content=values[":content"], revision=values[":next"])
        return {}

    def put_item(self, **request):
        if request["TableName"] == "records":
            item = decode_item(request["Item"])
            key = (item["pk"], item["sk"])
            if request.get("ConditionExpression") and key in self.items:
                raise client_error("ConditionalCheckFailedException")
            self.calls.append(("put", deepcopy(request)))
            self.put(item)
            return {}
        self.calls.append(("audit", deepcopy(request)))
        assert request["TableName"] == "audit"
        item = decode_item(request["Item"])
        if item["outcome"] == self.fail_audit:
            raise client_error("InternalServerError")
        if any(old["pk"] == item["pk"] and old["sk"] == item["sk"] for old in self.audit):
            raise client_error("ConditionalCheckFailedException")
        self.audit.append(item)
        return {}

    def query(self, **request):
        self.calls.append(("query", deepcopy(request)))
        values = decode_item(request["ExpressionAttributeValues"])
        rows = [
            item
            for (pk, sk), item in sorted(self.items.items())
            if pk == values[":company"] and sk.startswith(values[":prefix"])
        ]
        if request.get("ExclusiveStartKey"):
            start = decode_item(request["ExclusiveStartKey"])["sk"]
            rows = [item for item in rows if item["sk"] > start]
        page = rows[: request["Limit"]]
        fields = request["ExpressionAttributeNames"].values()
        result = {
            "Items": [encode_item({k: v for k, v in row.items() if k in fields}) for row in page]
        }
        if len(rows) > len(page):
            result["LastEvaluatedKey"] = encode_item({k: page[-1][k] for k in ("pk", "sk")})
        return result
