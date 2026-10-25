"""Test-only identity provider: fictitious keys, a JWKS document and token minting. Never used by the application."""

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

TEST_ISSUER = "https://test-project.supabase.co/auth/v1"
TEST_AUDIENCE = "authenticated"
OTHER_ISSUER = "https://another-project.supabase.co/auth/v1"

_EC_KEY = ec.generate_private_key(ec.SECP256R1())
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SIGNING_KEYS = {"test-ec-kid": (_EC_KEY, "ES256"), "test-rsa-kid": (_RSA_KEY, "RS256")}


def jwks_document() -> dict:
    keys = []
    for kid, (private_key, algorithm) in SIGNING_KEYS.items():
        public_key = private_key.public_key()
        factory = jwt.algorithms.ECAlgorithm if algorithm == "ES256" else jwt.algorithms.RSAAlgorithm
        jwk = json.loads(factory.to_jwk(public_key))
        jwk.update({"kid": kid, "alg": algorithm, "use": "sig"})
        keys.append(jwk)
    return {"keys": keys}


def public_key_resolver(kid: str):  # type: ignore[no-untyped-def]
    private_key, _ = SIGNING_KEYS[kid]
    return private_key.public_key()


def make_token(
    subject: str | None = None,
    *,
    kid: str = "test-ec-kid",
    algorithm: str | None = None,
    issuer: str = TEST_ISSUER,
    audience: str = TEST_AUDIENCE,
    expires_in: int = 3600,
    role: str = "authenticated",
    email: str | None = "person@example.com",
    signing_key=None,  # type: ignore[no-untyped-def]
    extra_claims: dict | None = None,
) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject or str(uuid.uuid4()),
        "iat": now,
        "exp": now + expires_in,
        "role": role,
    }
    if email is not None:
        claims["email"] = email
    if extra_claims:
        claims.update(extra_claims)
    known = SIGNING_KEYS.get(kid)
    key = signing_key if signing_key is not None else (known[0] if known else None)
    default_algorithm = known[1] if known else "ES256"
    return jwt.encode(
        claims,
        key,
        algorithm=algorithm or default_algorithm,
        headers={"kid": kid},
    )


class JwksServer:
    """Serves the test JWKS over local HTTP, as the Supabase endpoint would. Used to exercise PyJWKClient for real."""

    def __init__(self) -> None:
        document = json.dumps(jwks_document()).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(document)))
                self.end_headers()
                self.wfile.write(document)

            def log_message(self, *args) -> None:  # type: ignore[no-untyped-def]
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/auth/v1/.well-known/jwks.json"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "JwksServer":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:  # type: ignore[no-untyped-def]
        self._server.shutdown()
        self._server.server_close()
