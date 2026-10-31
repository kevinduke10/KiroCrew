"""Real RSA signature checks with an in-memory JWKS and fixed verifier clock."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError
from kirocrew_custom.saas.auth import (
    USER_SCOPE,
    AuthenticationError,
    CognitoVerifier,
    DependencyUnavailable,
    cognito_issuer,
)
from saas_helpers import ISSUER, NOW, OWNER, client_error


@pytest.fixture(scope="module")
def signing_key():
    # Crypto randomness is key material only; no assertion depends on its value.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def auth(monkeypatch, signing_key):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(jwt.api_jwt, "datetime", FixedDateTime)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    public.update(kid="test-key", use="sig", alg="RS256")
    jwks = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", cache_keys=False)
    monkeypatch.setattr(jwks, "fetch_data", lambda: {"keys": [public]})
    cognito = Mock()
    cognito.get_user.return_value = {"UserAttributes": [{"Name": "sub", "Value": OWNER.subject}]}
    verifier = CognitoVerifier(
        region="us-east-1",
        pool_id="us-east-1_TestPool",
        client_id="app-client",
        cognito=cognito,
        jwks=jwks,
    )
    claims = {
        "iss": ISSUER,
        "sub": OWNER.subject,
        "client_id": "app-client",
        "token_use": "access",
        "scope": "openid " + USER_SCOPE,
        "iat": int((NOW - timedelta(minutes=1)).timestamp()),
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
    }

    def token(**changes):
        return jwt.encode(
            {**claims, **changes}, signing_key, algorithm="RS256", headers={"kid": "test-key"}
        )

    return verifier, cognito, token, claims


def test_verified_access_token_requires_live_matching_user(auth):
    verifier, cognito, token, _ = auth
    encoded = token()
    assert verifier.verify(encoded) == OWNER
    cognito.get_user.assert_called_once_with(AccessToken=encoded)


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://attacker.invalid"},
        {"client_id": "other-app"},
        {"token_use": "id"},
        {"scope": "openid"},
        {"sub": ""},
        {"exp": 1},
        {"exp": None},
        {"iat": 9999999999},
        {"nbf": 9999999999},
        {"aud": "unexpected-resource"},
    ],
)
def test_unaccepted_claims_never_call_cognito(auth, change):
    verifier, cognito, token, _ = auth
    with pytest.raises(AuthenticationError):
        verifier.verify(token(**change))
    cognito.get_user.assert_not_called()


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "sub", "client_id", "token_use"])
def test_required_claims_are_not_optional(auth, signing_key, claim):
    verifier, cognito, _, claims = auth
    claims.pop(claim)
    encoded = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(AuthenticationError):
        verifier.verify(encoded)
    cognito.get_user.assert_not_called()


def test_tampered_signature_unknown_key_and_algorithm_are_rejected(auth, signing_key):
    verifier, cognito, token, claims = auth
    encoded = token()
    head, payload, signature = encoded.split(".")
    damaged = ("A" if signature[0] != "A" else "B") + signature[1:]
    candidates = [
        f"{head}.{payload}.{damaged}",
        jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "unknown"}),
        jwt.encode(
            claims,
            "test-only-secret-at-least-thirty-two-bytes",
            algorithm="HS256",
            headers={"kid": "test-key"},
        ),
        "not-a-jwt",
    ]
    for candidate in candidates:
        with pytest.raises(AuthenticationError):
            verifier.verify(candidate)
    cognito.get_user.assert_not_called()


@pytest.mark.parametrize("code", ["NotAuthorizedException", "UserNotFoundException"])
def test_revoked_or_disabled_user_fails_live_confirmation(auth, code):
    verifier, cognito, token, _ = auth
    cognito.get_user.side_effect = client_error(code)
    with pytest.raises(AuthenticationError):
        verifier.verify(token())


def test_live_subject_must_match_signed_subject(auth):
    verifier, cognito, token, _ = auth
    cognito.get_user.return_value = {"UserAttributes": [{"Name": "sub", "Value": "other-user"}]}
    with pytest.raises(AuthenticationError):
        verifier.verify(token())


def test_jwks_and_cognito_outage_fail_closed(auth, monkeypatch):
    verifier, cognito, token, _ = auth
    cognito.get_user.side_effect = client_error("TooManyRequestsException")
    with pytest.raises(DependencyUnavailable):
        verifier.verify(token())
    monkeypatch.setattr(
        verifier.jwks,
        "get_signing_key_from_jwt",
        Mock(side_effect=PyJWKClientConnectionError("offline")),
    )
    with pytest.raises(DependencyUnavailable):
        verifier.verify(token())


def test_resource_bound_audience_requires_explicit_configuration(auth):
    verifier, _, token, _ = auth
    verifier.audience = "https://api.example.test"
    assert verifier.verify(token(aud=verifier.audience)) == OWNER


@pytest.mark.parametrize(
    "region,pool",
    [
        ("us-east-1", "https://attacker.invalid"),
        ("us-east-1.evil", "pool"),
        ("us-east-1", "us-west-2_TestPool"),
    ],
)
def test_issuer_configuration_cannot_select_arbitrary_jwks_hosts(region, pool):
    with pytest.raises(ValueError):
        cognito_issuer(region, pool)


def test_signed_token_to_authorized_persisted_content_roundtrip(auth):
    from itertools import count

    from kirocrew_custom.saas.api import LoginConfig, PooledApi
    from kirocrew_custom.saas.dynamo import DynamoAudit
    from kirocrew_custom.saas.service import ResourceService
    from saas_helpers import KEY, MemoryDynamo

    verifier, _, token, _ = auth
    client = MemoryDynamo()
    ids = count(1)
    service = ResourceService(
        client=client,
        records_table="records",
        audit=DynamoAudit(client, "audit"),
        clock=lambda: NOW,
        new_id=lambda: f"operation-{next(ids)}",
    )
    app = PooledApi(
        verifier=verifier,
        service=service,
        login=LoginConfig(
            ISSUER,
            "app-client",
            "https://login.example.test",
            "https://app.example.test/callback",
            "https://app.example.test/",
        ),
    )
    request = {
        "version": "2.0",
        "requestContext": {"http": {"method": "GET"}},
        "rawPath": f"/companies/{KEY.company_id}/organizations/{KEY.organization_id}/spaces/{KEY.space_id}/resources/{KEY.resource_id}",
        "headers": {"authorization": "Bearer " + token()},
    }
    assert app.handle(request)["statusCode"] == 200
    request["headers"]["authorization"] = "Bearer " + token(token_use="id")
    assert app.handle(request)["statusCode"] == 401
