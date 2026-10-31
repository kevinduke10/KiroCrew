"""Lazy Lambda composition from trusted deployment configuration."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config

from .api import LoginConfig, PooledApi, _response
from .auth import CognitoVerifier
from .control import ControlService
from .control_api import ControlApi
from .dynamo import DynamoAudit
from .platform import PlatformDirectory
from .service import ResourceService

AWS_CONNECT_TIMEOUT_SECONDS = 3
AWS_READ_TIMEOUT_SECONDS = 5
AWS_TOTAL_ATTEMPTS = 2


def _https_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("An HTTPS URL without credentials or fragment is required")
    return value


@lru_cache(maxsize=1)
def application() -> PooledApi:
    region = os.environ["AWS_REGION"]
    client_config = Config(
        connect_timeout=AWS_CONNECT_TIMEOUT_SECONDS,
        read_timeout=AWS_READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "total_max_attempts": AWS_TOTAL_ATTEMPTS},
    )
    dynamo = boto3.client("dynamodb", region_name=region, config=client_config)
    cognito = boto3.client("cognito-idp", region_name=region, config=client_config)
    verifier = CognitoVerifier(
        region=region,
        pool_id=os.environ["COGNITO_POOL_ID"],
        client_id=os.environ["COGNITO_CLIENT_ID"],
        cognito=cognito,
        audience=os.environ.get("COGNITO_RESOURCE_AUDIENCE") or None,
    )
    domain = _https_url(os.environ["COGNITO_DOMAIN"])
    if urlsplit(domain).path not in {"", "/"} or urlsplit(domain).query:
        raise ValueError("Cognito domain must be an origin")
    login = LoginConfig(
        issuer=verifier.issuer,
        client_id=verifier.client_id,
        domain=domain.rstrip("/"),
        callback_url=_https_url(os.environ["COGNITO_CALLBACK_URL"]),
        logout_url=_https_url(os.environ["COGNITO_LOGOUT_URL"]),
        knowledge_enabled=os.environ.get("KNOWLEDGE_ENABLED") == "true",
    )
    platform = PlatformDirectory(
        dynamo,
        roles_table=os.environ["PLATFORM_ROLES_TABLE"],
        control_table=os.environ["CONTROL_TABLE"],
        records_table=os.environ["RECORDS_TABLE"],
    )
    service = ResourceService(
        client=dynamo,
        records_table=os.environ["RECORDS_TABLE"],
        audit=DynamoAudit(dynamo, os.environ["AUDIT_TABLE"]),
        clock=lambda: datetime.now(timezone.utc),
        new_id=lambda: uuid4().hex,
        platform=platform,
    )
    if os.environ.get("KNOWLEDGE_PLANE") == "true":
        from .knowledge.api import KnowledgeApi
        from .knowledge.bedrock import BedrockKnowledge
        from .knowledge.records import KnowledgeStore
        from .knowledge.service import KnowledgeService

        backend = BedrockKnowledge(
            s3=boto3.client("s3", region_name=region, config=client_config),
            agent=boto3.client("bedrock-agent", region_name=region, config=client_config),
            runtime=boto3.client("bedrock-agent-runtime", region_name=region, config=client_config),
            bucket=os.environ["KNOWLEDGE_BUCKET"],
            account_id=os.environ["AWS_ACCOUNT_ID"],
            knowledge_base_id=os.environ["KNOWLEDGE_BASE_ID"],
            data_source_id=os.environ["KNOWLEDGE_DATA_SOURCE_ID"],
        )
        knowledge = KnowledgeService(
            service, KnowledgeStore(dynamo, os.environ["KNOWLEDGE_TABLE"]), backend
        )
        return KnowledgeApi(knowledge=knowledge, verifier=verifier, service=service, login=login)
    if os.environ.get("CONTROL_PLANE") == "true":
        if platform is None:
            raise ValueError("Control plane requires authoritative platform tables")
        control = ControlService(
            directory=platform,
            audit=service.audit,
            cognito=cognito,
            pool_id=os.environ["COGNITO_POOL_ID"],
            issuer=verifier.issuer,
            clock=service.clock,
            new_id=service.new_id,
        )
        return ControlApi(control=control, verifier=verifier, service=service, login=login)
    return PooledApi(verifier=verifier, service=service, login=login)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        from .web_assets import asset_response

        app = application()
        asset = asset_response(event, app.login.domain)
        return asset if asset is not None else app.handle(event)
    except Exception:
        return _response(503, {"error": "service_unavailable"})
