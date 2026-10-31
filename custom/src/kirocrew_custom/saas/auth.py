"""Cognito access-token authentication, independent of company authorization."""

from __future__ import annotations

import re
from typing import Any

import jwt
from botocore.exceptions import BotoCoreError, ClientError
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

from .access import Identity

JWKS_TIMEOUT_SECONDS = 5
JWKS_CACHE_SECONDS = 300
MAX_TOKEN_LENGTH = 16384
USER_SCOPE = "aws.cognito.signin.user.admin"


class AuthenticationError(PermissionError):
    """No accepted, current Cognito access token was provided."""


class DependencyUnavailable(RuntimeError):
    """A required identity, persistence or audit dependency is unavailable."""


def cognito_issuer(region: str, pool_id: str) -> str:
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", region):
        raise ValueError("Invalid Cognito region")
    if not re.fullmatch(re.escape(region) + r"_[A-Za-z0-9]+", pool_id):
        raise ValueError("Invalid Cognito user pool")
    suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    return f"https://cognito-idp.{region}.{suffix}/{pool_id}"


class CognitoVerifier:
    def __init__(
        self,
        *,
        region: str,
        pool_id: str,
        client_id: str,
        cognito: Any,
        audience: str | None = None,
        jwks: Any = None,
    ) -> None:
        self.issuer = cognito_issuer(region, pool_id)
        if not client_id:
            raise ValueError("Cognito client ID is required")
        self.client_id = client_id
        self.audience = audience
        self.cognito = cognito
        self.jwks = (
            jwks
            if jwks is not None
            else PyJWKClient(
                self.issuer + "/.well-known/jwks.json",
                timeout=JWKS_TIMEOUT_SECONDS,
                lifespan=JWKS_CACHE_SECONDS,
                cache_keys=False,
            )
        )

    def verify(self, token: str) -> Identity:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
            raise AuthenticationError("Authentication required")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise AuthenticationError("Authentication required")
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iat", "iss", "sub", "client_id", "token_use"]},
            )
            if (
                claims["token_use"] != "access"
                or claims["client_id"] != self.client_id
                or not isinstance(claims["sub"], str)
                or not claims["sub"]
                or not isinstance(claims.get("scope"), str)
                or USER_SCOPE not in claims["scope"].split()
            ):
                raise AuthenticationError("Authentication required")
        except PyJWKClientConnectionError as exc:
            raise DependencyUnavailable("Identity service unavailable") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Authentication required") from exc
        try:
            user = self.cognito.get_user(AccessToken=token)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {
                "NotAuthorizedException",
                "UserNotFoundException",
                "UserNotConfirmedException",
            }:
                raise AuthenticationError("Authentication required") from exc
            raise DependencyUnavailable("Identity service unavailable") from exc
        except BotoCoreError as exc:
            raise DependencyUnavailable("Identity service unavailable") from exc
        subjects = [
            attribute.get("Value")
            for attribute in user.get("UserAttributes", [])
            if attribute.get("Name") == "sub"
        ]
        if subjects != [claims["sub"]]:
            raise AuthenticationError("Authentication required")
        return Identity(self.issuer, claims["sub"])
