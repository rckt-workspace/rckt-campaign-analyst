"""Simulated Supabase Auth for end-to-end tests. It issues real ES256 tokens with fictitious keys.

It never contacts Supabase and is never part of the application. Only the test webServer starts it.
Endpoints (Supabase-compatible subset):
  POST /auth/v1/token?grant_type=password        sign in with email and password
  POST /auth/v1/token?grant_type=refresh_token   refresh a session
  GET  /auth/v1/user                             user for a bearer token
  POST /auth/v1/logout                           end the session
  GET  /auth/v1/.well-known/jwks.json            public keys used to verify the tokens
Test controls:
  GET  /__health   readiness
  POST /__reset    back to defaults; refresh allowed
  POST /__control  {"refresh": "ok" | "revoked"}   revoked makes refresh fail, as an expired or revoked session would
"""

import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

PORT = 18090
ISSUER = f"http://127.0.0.1:{PORT}/auth/v1"
AUDIENCE = "authenticated"
KID = "e2e-ec-kid"
IDENTITIES = json.loads((Path(__file__).parent / "identities.json").read_text(encoding="utf-8"))
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())

state = {"refresh": "ok"}
sessions: dict[str, dict] = {}  # refresh token -> user key
revoked_subjects: set[str] = set()  # subjects whose sessions were ended by logout


def by_key(key: str) -> dict:
    return next(user for user in IDENTITIES["users"] if user["key"] == key)


def by_email(email: str) -> dict | None:
    return next((user for user in IDENTITIES["users"] if user["email"] == email.lower()), None)


def by_subject(subject: str) -> dict | None:
    return next((user for user in IDENTITIES["users"] if user["subject"] == subject), None)


def jwks() -> dict:
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(PRIVATE_KEY.public_key()))
    public.update({"kid": KID, "alg": "ES256", "use": "sig"})
    return {"keys": [public]}


def issue(user: dict) -> dict:
    lifetime = int(user.get("accessTokenSeconds", 3600))
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": user["subject"],
        "email": user["email"],
        "role": "authenticated",
        "iat": now,
        "exp": now + lifetime,
    }
    access_token = jwt.encode(claims, PRIVATE_KEY, algorithm="ES256", headers={"kid": KID})
    refresh_token = uuid.uuid4().hex
    sessions[refresh_token] = {"key": user["key"]}
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": lifetime,
        "expires_at": now + lifetime,
        "refresh_token": refresh_token,
        "user": user_payload(user),
    }


def user_payload(user: dict) -> dict:
    return {
        "id": user["subject"],
        "aud": AUDIENCE,
        "role": "authenticated",
        "email": user["email"],
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": {},
        "created_at": "2026-10-01T00:00:00Z",
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict | None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    def _bearer_user(self) -> dict | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        try:
            claims = jwt.decode(header[7:], PRIVATE_KEY.public_key(), algorithms=["ES256"], audience=AUDIENCE, issuer=ISSUER)
        except jwt.PyJWTError:
            return None
        return by_subject(claims["sub"])

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/__health":
            return self._json(200, {"status": "ok"})
        if path == "/auth/v1/.well-known/jwks.json":
            return self._json(200, jwks())
        if path == "/auth/v1/user":
            user = self._bearer_user()
            if user and user["subject"] in revoked_subjects:
                user = None
            return self._json(200, user_payload(user)) if user else self._json(401, {"message": "invalid JWT"})
        self._json(404, {"message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/__reset":
            state["refresh"] = "ok"
            sessions.clear()
            revoked_subjects.clear()
            return self._json(200, {"status": "reset"})
        if parsed.path == "/__control":
            body = self._body()
            if body.get("refresh") not in ("ok", "revoked"):
                return self._json(400, {"message": "invalid control"})
            state["refresh"] = body["refresh"]
            return self._json(200, state)
        if parsed.path == "/auth/v1/token":
            grant = query.get("grant_type", [""])[0]
            body = self._body()
            if grant == "password":
                user = by_email(str(body.get("email", "")))
                if user is None or body.get("password") != IDENTITIES["password"]:
                    return self._json(400, {"error": "invalid_grant", "error_description": "Invalid login credentials"})
                return self._json(200, issue(user))
            if grant == "refresh_token":
                entry = sessions.get(str(body.get("refresh_token", "")))
                if entry is None or state["refresh"] == "revoked":
                    return self._json(400, {"error": "invalid_grant", "error_description": "Invalid Refresh Token"})
                return self._json(200, issue(by_key(entry["key"])))
            return self._json(400, {"error": "unsupported_grant_type"})
        if parsed.path == "/auth/v1/logout":
            user = self._bearer_user()
            if user:
                revoked_subjects.add(user["subject"])
            return self._json(204, None)
        self._json(404, {"message": "not found"})

    def log_message(self, *args) -> None:  # type: ignore[no-untyped-def]
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
