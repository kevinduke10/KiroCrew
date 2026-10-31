"""SDK shape and template boundaries without AWS credentials or network."""

import hashlib
from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber
from kirocrew_custom.saas.dynamo import encode_item
from kirocrew_custom.saas.knowledge.bedrock import BedrockKnowledge
from kirocrew_custom.saas.knowledge.records import Document, KnowledgeStore, document_item
from test_saas_template import template


def client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def test_real_bedrock_and_s3_sdk_shapes_for_upload_status_and_filtered_search():
    s3, agent, runtime = client("s3"), client("bedrock-agent"), client("bedrock-agent-runtime")
    backend = BedrockKnowledge(
        s3=s3,
        agent=agent,
        runtime=runtime,
        bucket="knowledge-test",
        account_id="123456789012",
        knowledge_base_id="KB12345678",
        data_source_id="DS12345678",
    )
    document = Document(
        "company-a",
        "doc",
        None,
        "Policy",
        "txt",
        hashlib.sha256(b"policy").hexdigest(),
        "fingerprint",
        "revision",
    )
    detail = {
        "knowledgeBaseId": "KB12345678",
        "dataSourceId": "DS12345678",
        "identifier": {"dataSourceType": "S3", "s3": {"uri": backend.uri(document)}},
        "status": "INDEXED",
        "updatedAt": datetime(2026, 10, 29, tzinfo=timezone.utc),
    }
    with Stubber(s3) as s, Stubber(agent) as a, Stubber(runtime) as r:
        s.add_response("put_object", {})
        s.add_response("put_object", {})
        a.add_response("ingest_knowledge_base_documents", {"documentDetails": [detail]})
        assert backend.ingest(document, b"policy", "operation") == "INDEXED"
        a.add_response("get_knowledge_base_documents", {"documentDetails": [detail]})
        assert backend.status(document) == "INDEXED"
        r.add_response(
            "retrieve",
            {"retrievalResults": []},
            {
                "knowledgeBaseId": "KB12345678",
                "retrievalQuery": {"text": "policy"},
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {
                        "numberOfResults": 10,
                        "filter": {
                            "andAll": [
                                {"equals": {"key": "company_id", "value": "company-a"}},
                                {"in": {"key": "document_id", "value": ["doc"]}},
                            ]
                        },
                    }
                },
            },
        )
        assert backend.retrieve("company-a", ["doc"], "policy") == []
        for stub in (s, a, r):
            stub.assert_no_pending_responses()


def test_dynamo_manifest_conditional_updates_have_valid_shapes():
    dynamo = client("dynamodb")
    store = KnowledgeStore(dynamo, "knowledge")
    document = Document("company-a", "doc", "hr", "Policy", "txt", "digest", "fingerprint", "v1")
    with Stubber(dynamo) as stub:
        stub.add_response("put_item", {})
        assert store.reserve(document) == document
        stub.add_response("get_item", {"Item": encode_item(document_item(document))})
        assert store.get("company-a", "doc") == document
        stub.add_response("update_item", {})
        assert store.share(document, ("child",), True, "v2").shared_children == ("child",)
        stub.add_response("query", {"Items": [encode_item(document_item(document))]})
        assert store.documents("company-a") == [document]
        stub.assert_no_pending_responses()


def test_knowledge_role_cannot_modify_membership_or_read_originals_directly():
    resources = template()["Resources"]
    function = resources["KnowledgeApiFunction"]
    assert function["Condition"] == "KnowledgeConfigured"
    statements = function["Properties"]["Policies"][0]["Statement"]
    for statement in statements:
        if {"GetAtt": "RecordsTable.Arn"} in (
            statement["Resource"]
            if isinstance(statement["Resource"], list)
            else [statement["Resource"]]
        ):
            assert statement["Action"] == ["dynamodb:GetItem"]
        assert "s3:GetObject" not in statement["Action"]
        assert "bedrock:RetrieveAndGenerate" not in statement["Action"]
    bucket = resources["KnowledgeDocumentsBucket"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert bucket["BucketEncryption"]["ServerSideEncryptionConfiguration"]
