"""Cryptographic verification of Supabase-style access tokens."""

import time
import uuid

import jwt
import pytest

from app.security.tokens import (
    ALLOWED_ALGORITHMS,
    AuthenticationError,
    AuthProviderUnavailableError,
    SupabaseTokenVerifier,
)
from tests.auth_helpers import (
    OTHER_ISSUER,
    TEST_AUDIENCE,
    TEST_ISSUER,
    JwksServer,
    make_token,
    public_key_resolver,
)


@pytest.fixture
def verifier() -> SupabaseTokenVerifier:
    return SupabaseTokenVerifier(issuer=TEST_ISSUER, audience=TEST_AUDIENCE, resolve_key=public_key_resolver)


def test_valid_es256_token_is_accepted(verifier):
    subject = str(uuid.uuid4())
    identity = verifier.verify(make_token(subject, email="ana@cliente.com"))
    assert identity.subject == subject
    assert identity.email == "ana@cliente.com"


def test_valid_rs256_token_is_accepted(verifier):
    identity = verifier.verify(make_token("rsa-user", kid="test-rsa-kid"))
    assert identity.subject == "rsa-user"


def test_token_without_email_is_accepted_with_null_email(verifier):
    identity = verifier.verify(make_token("no-email", email=None))
    assert identity.email is None


def test_expired_token_is_rejected(verifier):
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(expires_in=-600))


def test_token_expired_beyond_leeway_is_rejected(verifier):
    # Leeway is 10 seconds; 60 seconds past expiry must fail.
    token = make_token(expires_in=-60)
    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_forged_signature_is_rejected(verifier):
    from cryptography.hazmat.primitives.asymmetric import ec

    attacker_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token("victim", signing_key=attacker_key))


def test_tampered_payload_is_rejected(verifier):
    token = make_token("someone")
    header, payload, signature = token.split(".")
    tampered_payload = jwt.utils.base64url_encode(
        b'{"iss":"%s","aud":"authenticated","sub":"admin","exp":9999999999,"role":"authenticated"}' % TEST_ISSUER.encode()
    ).decode()
    with pytest.raises(AuthenticationError):
        verifier.verify(f"{header}.{tampered_payload}.{signature}")


def test_wrong_issuer_is_rejected(verifier):
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(issuer=OTHER_ISSUER))


def test_token_from_another_project_is_rejected(verifier):
    # Same keys, different project: the issuer claim is the only difference.
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token("other-project-user", issuer="https://evil.supabase.co/auth/v1"))


def test_wrong_audience_is_rejected(verifier):
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(audience="service_role"))


def test_missing_subject_is_rejected(verifier):
    now = int(time.time())
    from tests.auth_helpers import SIGNING_KEYS

    key, _ = SIGNING_KEYS["test-ec-kid"]
    token = jwt.encode(
        {"iss": TEST_ISSUER, "aud": TEST_AUDIENCE, "iat": now, "exp": now + 60, "role": "authenticated"},
        key,
        algorithm="ES256",
        headers={"kid": "test-ec-kid"},
    )
    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_non_authenticated_role_is_rejected(verifier):
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token("anon-like", role="anon"))


def test_alg_none_is_rejected(verifier):
    now = int(time.time())
    token = jwt.encode(
        {"iss": TEST_ISSUER, "aud": TEST_AUDIENCE, "sub": "x", "iat": now, "exp": now + 60, "role": "authenticated"},
        None,
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_hs256_token_is_rejected_even_with_known_kid(verifier):
    # Classic algorithm-confusion attack: a shared-secret HMAC token must never pass for an asymmetric key.
    now = int(time.time())
    token = jwt.encode(
        {"iss": TEST_ISSUER, "aud": TEST_AUDIENCE, "sub": "x", "iat": now, "exp": now + 60, "role": "authenticated"},
        "shared-secret-attempt",
        algorithm="HS256",
        headers={"kid": "test-ec-kid"},
    )
    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_missing_kid_is_rejected(verifier):
    now = int(time.time())
    from tests.auth_helpers import SIGNING_KEYS

    key, _ = SIGNING_KEYS["test-ec-kid"]
    token = jwt.encode(
        {"iss": TEST_ISSUER, "aud": TEST_AUDIENCE, "sub": "x", "iat": now, "exp": now + 60, "role": "authenticated"},
        key,
        algorithm="ES256",
    )
    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_garbage_input_is_rejected(verifier):
    for token in ("", "not-a-jwt", "a.b.c", "eyJhbGciOiJIUzI1NiJ9.e30."):
        with pytest.raises(AuthenticationError):
            verifier.verify(token)


def test_allowed_algorithms_are_asymmetric_only():
    assert set(ALLOWED_ALGORITHMS) == {"ES256", "RS256"}


def test_verifier_fetches_keys_from_jwks_endpoint_and_rejects_unknown_kid():
    with JwksServer() as server:
        verifier = SupabaseTokenVerifier.from_jwks_url(jwks_url=server.url, issuer=TEST_ISSUER, audience=TEST_AUDIENCE)
        assert verifier.verify(make_token("via-jwks")).subject == "via-jwks"
        assert verifier.verify(make_token("rsa-via-jwks", kid="test-rsa-kid")).subject == "rsa-via-jwks"
        from cryptography.hazmat.primitives.asymmetric import ec

        unknown_key = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(AuthenticationError):
            verifier.verify(make_token("x", kid="unknown-kid", signing_key=unknown_key))


def test_unreachable_jwks_endpoint_is_an_outage_not_an_auth_decision():
    verifier = SupabaseTokenVerifier.from_jwks_url(
        jwks_url="http://127.0.0.1:1/auth/v1/.well-known/jwks.json", issuer=TEST_ISSUER, audience=TEST_AUDIENCE
    )
    with pytest.raises(AuthProviderUnavailableError):
        verifier.verify(make_token("unreachable"))
