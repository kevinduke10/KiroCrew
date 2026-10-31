"""Deployment composition is tested without credential discovery or network."""

from unittest.mock import Mock

import pytest
from kirocrew_custom.saas import bootstrap
from kirocrew_custom.saas.auth import cognito_issuer


def test_build_from_trusted_environment_has_bounded_clients(monkeypatch):
    settings = {
        "AWS_REGION": "us-east-1",
        "COGNITO_POOL_ID": "us-east-1_TestPool",
        "COGNITO_CLIENT_ID": "app-client",
        "COGNITO_DOMAIN": "https://login.example.test",
        "COGNITO_CALLBACK_URL": "https://app.example.test/callback",
        "COGNITO_LOGOUT_URL": "https://app.example.test/",
        "PLATFORM_ROLES_TABLE": "roles",
        "CONTROL_TABLE": "control",
        "RECORDS_TABLE": "records",
        "AUDIT_TABLE": "audit",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("COGNITO_RESOURCE_AUDIENCE", raising=False)
    monkeypatch.delenv("CONTROL_PLANE", raising=False)
    factory = Mock()
    monkeypatch.setattr(bootstrap.boto3, "client", factory)
    app = bootstrap.application.__wrapped__()
    assert app.login.issuer == cognito_issuer("us-east-1", "us-east-1_TestPool")
    assert {call.args[0] for call in factory.call_args_list} == {"dynamodb", "cognito-idp"}
    for call in factory.call_args_list:
        config = call.kwargs["config"]
        assert config.connect_timeout == bootstrap.AWS_CONNECT_TIMEOUT_SECONDS
        assert config.read_timeout == bootstrap.AWS_READ_TIMEOUT_SECONDS
        assert config.retries["total_max_attempts"] == bootstrap.AWS_TOTAL_ATTEMPTS


@pytest.mark.parametrize(
    "url",
    ["http://example.test", "https://user:pass@example.test", "https://example.test/#fragment"],
)
def test_invalid_login_urls_fail_before_serving_config(url):
    with pytest.raises(ValueError):
        bootstrap._https_url(url)


def test_lambda_configuration_failure_is_redacted(monkeypatch):
    monkeypatch.setattr(bootstrap, "application", Mock(side_effect=KeyError("sensitive config")))
    response = bootstrap.handler({}, None)
    assert response["statusCode"] == 503
    assert "sensitive" not in response["body"]
