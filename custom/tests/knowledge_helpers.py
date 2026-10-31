"""Offline AWS transports for the real knowledge adapters."""

import json
from copy import deepcopy
from itertools import count
from unittest.mock import Mock

from kirocrew_custom.saas.access import Identity, Membership
from kirocrew_custom.saas.api import LoginConfig
from kirocrew_custom.saas.dynamo import DynamoAudit, decode_item, encode_item
from kirocrew_custom.saas.knowledge.api import KnowledgeApi
from kirocrew_custom.saas.knowledge.bedrock import BedrockKnowledge
from kirocrew_custom.saas.knowledge.records import KnowledgeStore
from kirocrew_custom.saas.knowledge.service import KnowledgeService
from kirocrew_custom.saas.organizations import organization_key
from kirocrew_custom.saas.records import membership_item
from kirocrew_custom.saas.roles import Role
from kirocrew_custom.saas.service import ResourceService
from saas_helpers import ADMIN, ISSUER, KEY, NOW, OWNER, MemoryDynamo, client_error

HR_ADMIN = Identity(ISSUER, "hr-admin")


class KnowledgeDynamo(MemoryDynamo):
    def __init__(self):
        super().__init__()
        self.manifests = {}
        for org, parent in [
            ("root", None),
            ("hr", "root"),
            ("recruiting", "hr"),
            ("interns", "recruiting"),
            ("benefits", "hr"),
            ("finance", "root"),
        ]:
            self.put(
                {
                    **organization_key(KEY.company_id, org),
                    "type": "organization",
                    "schema": 1,
                    "company_id": KEY.company_id,
                    "organization_id": org,
                    "name": org,
                    "parent_id": parent,
                }
            )
        self.put(membership_item(Membership(HR_ADMIN, KEY.company_id)))
        self.put(
            membership_item(
                Membership(
                    HR_ADMIN, KEY.company_id, organization_id="hr", role=Role.ORGANIZATION_ADMIN
                )
            )
        )
        for org in ("root", "recruiting", "interns", "benefits", "finance"):
            self.put(membership_item(Membership(OWNER, KEY.company_id, organization_id=org)))

    def get_item(self, **request):
        if request["TableName"] != "knowledge":
            return super().get_item(**request)
        assert request["ConsistentRead"] is True
        key = decode_item(request["Key"])
        item = self.manifests.get((key["pk"], key["sk"]))
        return {"Item": encode_item(deepcopy(item))} if item else {}

    def put_item(self, **request):
        if request["TableName"] != "knowledge":
            return super().put_item(**request)
        item = decode_item(request["Item"])
        key = (item["pk"], item["sk"])
        if key in self.manifests:
            raise client_error("ConditionalCheckFailedException")
        self.manifests[key] = deepcopy(item)
        return {}

    def update_item(self, **request):
        if request["TableName"] != "knowledge":
            return super().update_item(**request)
        key, values = decode_item(request["Key"]), decode_item(request["ExpressionAttributeValues"])
        item = self.manifests[(key["pk"], key["sk"])]
        if item["revision"] != values[":expected"]:
            raise client_error("ConditionalCheckFailedException")
        item.update(
            shared_children=values[":children"], active=values[":active"], revision=values[":next"]
        )
        return {}

    def query(self, **request):
        if request["TableName"] != "knowledge":
            return super().query(**request)
        assert request["ConsistentRead"] is True
        values = decode_item(request["ExpressionAttributeValues"])
        rows = [
            item
            for (pk, sk), item in sorted(self.manifests.items())
            if pk == values[":company"] and sk.startswith(values[":prefix"])
        ]
        if request.get("ExclusiveStartKey"):
            after = decode_item(request["ExclusiveStartKey"])["sk"]
            rows = [item for item in rows if item["sk"] > after]
        page = rows[: request["Limit"]]
        result = {"Items": [encode_item(item) for item in page]}
        if len(rows) > len(page):
            result["LastEvaluatedKey"] = encode_item({k: page[-1][k] for k in ("pk", "sk")})
        return result


class KnowledgeCloud:
    def __init__(self):
        self.objects = {}
        self.results = []
        self.requests = []
        self.before_retrieve = None
        self.fail_ingest = False

    def put_object(self, **request):
        self.objects[request["Key"]] = request["Body"]
        return {}

    def detail(self, uri):
        return {
            "knowledgeBaseId": "KB12345678",
            "dataSourceId": "DS12345678",
            "identifier": {"dataSourceType": "S3", "s3": {"uri": uri}},
            "status": "INDEXED",
        }

    def ingest_knowledge_base_documents(self, **request):
        if self.fail_ingest:
            raise RuntimeError("sensitive provider error")
        doc = request["documents"][0]
        uri = doc["content"]["s3"]["s3Location"]["uri"]
        key = uri.removeprefix("s3://knowledge-test/")
        metadata = json.loads(self.objects[key + ".metadata.json"])["metadataAttributes"]
        self.results = [r for r in self.results if r["location"]["s3Location"]["uri"] != uri]
        self.results.append(
            {
                "metadata": metadata,
                "location": {"type": "S3", "s3Location": {"uri": uri}},
                "content": {"type": "TEXT", "text": self.objects[key].decode("utf-8")},
                "score": 0.9,
            }
        )
        return {"documentDetails": [self.detail(uri)]}

    def get_knowledge_base_documents(self, **request):
        return {"documentDetails": [self.detail(request["documentIdentifiers"][0]["s3"]["uri"])]}

    def retrieve(self, **request):
        self.requests.append(deepcopy(request))
        if self.before_retrieve:
            self.before_retrieve()
        # Deliberately ignore the filter: post-retrieval authorization must still hold.
        return {"retrievalResults": deepcopy(self.results)}


def setup_knowledge():
    client, cloud = KnowledgeDynamo(), KnowledgeCloud()
    ids = count(1)
    resources = ResourceService(
        client=client,
        records_table="records",
        audit=DynamoAudit(client, "audit"),
        clock=lambda: NOW,
        new_id=lambda: f"operation-{next(ids)}",
    )
    backend = BedrockKnowledge(
        s3=cloud,
        agent=cloud,
        runtime=cloud,
        bucket="knowledge-test",
        account_id="123456789012",
        knowledge_base_id="KB12345678",
        data_source_id="DS12345678",
    )
    service = KnowledgeService(resources, KnowledgeStore(client, "knowledge"), backend)
    verifier = Mock()
    verifier.verify.return_value = ADMIN
    api = KnowledgeApi(
        knowledge=service,
        verifier=verifier,
        service=resources,
        login=LoginConfig(
            ISSUER,
            "client",
            "https://login.example.test",
            "https://app.example.test/",
            "https://app.example.test/",
            True,
        ),
    )
    return client, cloud, service, api, verifier
