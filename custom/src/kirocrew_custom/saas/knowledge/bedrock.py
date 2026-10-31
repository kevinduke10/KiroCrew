"""Private originals and managed Bedrock ingestion/retrieval; no model defaults."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..records import canonical
from .records import Document

MAX_UPLOAD_BYTES = 1024 * 1024
MIME_TYPES = {"txt": "text/plain", "md": "text/markdown", "pdf": "application/pdf"}


def validate_upload(extension: str, data: bytes) -> None:
    if (
        not isinstance(extension, str)
        or extension not in MIME_TYPES
        or not 0 < len(data) <= MAX_UPLOAD_BYTES
    ):
        raise ValueError("Unsupported document or size")
    if extension == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF")
    elif not data.decode("utf-8").strip() or b"\x00" in data:
        raise ValueError("Invalid text")


class BedrockKnowledge:
    def __init__(
        self,
        *,
        s3: Any,
        agent: Any,
        runtime: Any,
        bucket: str,
        account_id: str,
        knowledge_base_id: str,
        data_source_id: str,
    ) -> None:
        if (
            not re.fullmatch(r"[0-9A-Za-z]{10}", knowledge_base_id)
            or not re.fullmatch(r"[0-9A-Za-z]{10}", data_source_id)
            or not re.fullmatch(r"[0-9]{12}", account_id)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
        ):
            raise ValueError("Invalid knowledge configuration")
        self.s3, self.agent, self.runtime = s3, agent, runtime
        self.bucket, self.account_id = bucket, account_id
        self.knowledge_base_id, self.data_source_id = knowledge_base_id, data_source_id

    def key(self, document: Document) -> str:
        return f"knowledge/{document.company_id}/{document.document_id}/{document.source_version}.{document.extension}"

    def uri(self, document: Document) -> str:
        return f"s3://{self.bucket}/{self.key(document)}"

    def metadata(self, document: Document) -> dict[str, str]:
        return {
            "company_id": document.company_id,
            "document_id": document.document_id,
            "source_version": document.source_version,
        }

    def ingest(self, document: Document, data: bytes, operation_id: str) -> str:
        validate_upload(document.extension, data)
        if hashlib.sha256(data).hexdigest() != document.source_version:
            raise ValueError("Document digest mismatch")
        for key, body, mime in (
            (self.key(document), data, MIME_TYPES[document.extension]),
            (
                self.key(document) + ".metadata.json",
                canonical({"metadataAttributes": self.metadata(document)}).encode("utf-8"),
                "application/json",
            ),
        ):
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=mime,
                ServerSideEncryption="AES256",
                ExpectedBucketOwner=self.account_id,
            )
        result = self.agent.ingest_knowledge_base_documents(
            knowledgeBaseId=self.knowledge_base_id,
            dataSourceId=self.data_source_id,
            clientToken=hashlib.sha256(operation_id.encode("utf-8")).hexdigest(),
            documents=[
                {
                    "content": {
                        "dataSourceType": "S3",
                        "s3": {"s3Location": {"uri": self.uri(document)}},
                    },
                    "metadata": {
                        "type": "S3_LOCATION",
                        "s3Location": {
                            "uri": self.uri(document) + ".metadata.json",
                            "bucketOwnerAccountId": self.account_id,
                        },
                    },
                }
            ],
        )
        return self._status(result, document)

    def status(self, document: Document) -> str:
        result = self.agent.get_knowledge_base_documents(
            knowledgeBaseId=self.knowledge_base_id,
            dataSourceId=self.data_source_id,
            documentIdentifiers=[{"dataSourceType": "S3", "s3": {"uri": self.uri(document)}}],
        )
        return self._status(result, document)

    def _status(self, result: dict[str, Any], document: Document) -> str:
        details = result.get("documentDetails", [])
        if len(details) != 1:
            raise RuntimeError("Invalid ingestion response")
        detail = details[0]
        status = detail.get("status", "")
        if (
            detail.get("knowledgeBaseId") != self.knowledge_base_id
            or detail.get("dataSourceId") != self.data_source_id
            or detail.get("identifier")
            != {"dataSourceType": "S3", "s3": {"uri": self.uri(document)}}
            or not isinstance(status, str)
            or not re.fullmatch(r"[A-Z_]{1,64}", status)
        ):
            raise RuntimeError("Invalid ingestion response")
        return status

    def retrieve(self, company: str, documents: list[str], query: str) -> list[dict[str, Any]]:
        if not documents:
            return []
        results = []
        for start in range(0, len(documents), 20):
            response = self.runtime.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": 10,
                        "filter": {
                            "andAll": [
                                {"equals": {"key": "company_id", "value": company}},
                                {
                                    "in": {
                                        "key": "document_id",
                                        "value": documents[start : start + 20],
                                    }
                                },
                            ]
                        },
                    }
                },
            )
            results.extend(response.get("retrievalResults", [])[:10])
        return results
