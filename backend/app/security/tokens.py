"""Verification of Supabase Auth access tokens.

Only asymmetric algorithms are accepted, and the algorithm is taken from this module, never from the token.
Keys come from the project's JWKS, so rotation works without a shared secret in this service.
Every failure is reported with a generic error; the reason is never returned to the caller.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

ALLOWED_ALGORITHMS = ("ES256", "RS256")
EXPECTED_ROLE_CLAIM = "authenticated"
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]
LEEWAY_SECONDS = 10


class AuthenticationError(Exception):
    """The token is not acceptable. Callers answer 401 and never explain why."""


class AuthProviderUnavailableError(Exception):
    """Keys could not be fetched. Callers answer 503: the request is refused, not treated as anonymous."""


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    email: str | None


KeyResolver = Callable[[str], Any]


class SupabaseTokenVerifier:
    def __init__(self, *, issuer: str, audience: str, resolve_key: KeyResolver) -> None:
        self._issuer = issuer
        self._audience = audience
        self._resolve_key = resolve_key

    @classmethod
    def from_jwks_url(cls, *, jwks_url: str, issuer: str, audience: str) -> "SupabaseTokenVerifier":
        client = PyJWKClient(jwks_url, cache_keys=True, lifespan=600, timeout=5)

        def resolve(kid: str) -> Any:
            try:
                return client.get_signing_key(kid).key
            except jwt.PyJWKClientConnectionError as exc:
                # The JWKS endpoint is unreachable: a service problem, answered with 503.
                raise AuthProviderUnavailableError from exc
            except jwt.PyJWKClientError as exc:
                # Unknown kid: the caller sent a token we cannot verify. Attackers must not turn this into 503.
                raise AuthenticationError from exc

        return cls(issuer=issuer, audience=audience, resolve_key=resolve)

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError from exc

        algorithm = header.get("alg")
        kid = header.get("kid")
        if algorithm not in ALLOWED_ALGORITHMS or not isinstance(kid, str) or not kid:
            raise AuthenticationError

        key = self._resolve_key(kid)  # may raise AuthProviderUnavailableError

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError
        if claims.get("role") != EXPECTED_ROLE_CLAIM:
            raise AuthenticationError
        email = claims.get("email")
        return VerifiedIdentity(subject=subject, email=email if isinstance(email, str) else None)
