"""Table-aware deterministic transport for company control transactions."""

from copy import deepcopy

from kirocrew_custom.saas.access import Identity
from kirocrew_custom.saas.dynamo import decode_item, encode_item
from kirocrew_custom.saas.platform import company_record_key, identity_digest, platform_role_item
from saas_helpers import ISSUER, OWNER, MemoryDynamo, client_error

SUPERMAN = Identity(ISSUER, "operator")


class PlatformDynamo(MemoryDynamo):
    def __init__(self):
        super().__init__()
        self.tables = {"records": self.items, "roles": {}, "control": {}}
        self.transactions = 0
        self.fail_transaction = False
        self.seed("roles", platform_role_item(SUPERMAN, active=True))
        for company in ("company-a", "company-b"):
            self.seed(
                "control",
                {
                    **company_record_key(company),
                    "schema": 1,
                    "company_id": company,
                    "name": company,
                    "active": True,
                    "revision": "v1",
                },
            )
        self.seed("control", {"pk": "USER#" + identity_digest(OWNER), "sk": "company-a"})

    def seed(self, table, item):
        self.tables[table][item["pk"], item["sk"]] = deepcopy(item)

    def get_item(self, **request):
        if request["TableName"] == "records":
            return super().get_item(**request)
        self.calls.append(("get", deepcopy(request)))
        assert request["ConsistentRead"] is True
        key = decode_item(request["Key"])
        item = self.tables[request["TableName"]].get((key["pk"], key["sk"]))
        return {"Item": encode_item(deepcopy(item))} if item else {}

    def query(self, **request):
        self.calls.append(("query", deepcopy(request)))
        assert request["ConsistentRead"] is True
        partition = decode_item(request["ExpressionAttributeValues"])[":pk"]
        start = (
            decode_item(request["ExclusiveStartKey"])["sk"]
            if "ExclusiveStartKey" in request
            else ""
        )
        items = sorted(
            (
                i
                for (pk, sk), i in self.tables[request["TableName"]].items()
                if pk == partition and sk > start
            ),
            key=lambda i: i["sk"],
        )
        selected = items[: request["Limit"]]
        response = {"Items": [encode_item(deepcopy(item)) for item in selected]}
        if len(items) > len(selected):
            response["LastEvaluatedKey"] = encode_item({k: selected[-1][k] for k in ("pk", "sk")})
        return response

    def transact_write_items(self, **request):
        self.calls.append(("transaction", deepcopy(request)))
        self.transactions += 1
        if self.fail_transaction:
            raise client_error("InternalServerError")
        puts = []
        for write in request["TransactItems"]:
            kind, operation = next(iter(write.items()))
            item = decode_item(operation.get("Item", operation.get("Key")))
            table = self.tables[operation["TableName"]]
            existing = table.get((item["pk"], item["sk"]))
            condition = operation["ConditionExpression"]
            if condition == "attribute_not_exists(pk)":
                allowed = existing is None
            elif condition == "attribute_exists(pk)":
                allowed = existing is not None
            elif condition == "#revision = :expected":
                allowed = (
                    existing
                    and existing["revision"]
                    == decode_item(operation["ExpressionAttributeValues"])[":expected"]
                )
            elif condition == "#active = :yes":
                allowed = existing and existing["active"] is True
            else:
                raise AssertionError(condition)
            if not allowed:
                raise client_error("TransactionCanceledException")
            if kind == "Put":
                puts.append((operation["TableName"], item))
        for table, item in puts:
            self.seed(table, item)
        return {}
