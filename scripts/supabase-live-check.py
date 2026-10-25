"""Live check of the Supabase Auth integration against a dedicated RCKT development project.

Opt-in only: it never runs in CI and never runs by default. It prints PASS/FAIL per check and never prints
tokens, keys or passwords. Standard library only.

Required environment (set in your shell, never committed):
  SUPABASE_URL                   https://<project-ref>.supabase.co   (dedicated RCKT dev project)
  SUPABASE_PUBLISHABLE_KEY       publishable key of that project      (never a service_role key)
  LIVE_TEST_EMAIL                analyst test user created in that project
  LIVE_TEST_PASSWORD             its password
  LIVE_ORGANIZATION_SLUG         organization where that user is an analyst (see docs/PHASE2_AUTH_LIVE.md)
Optional:
  LIVE_FASTAPI_URL               running FastAPI, default http://127.0.0.1:8000 (reachable from this machine)
  LIVE_VIEWER_EMAIL, LIVE_VIEWER_PASSWORD   a client_viewer user in the same organization (checks the 403 rule)

The analysis check calls FastAPI, which calls OpenRouter. Start FastAPI with OPENROUTER_BASE_URL pointing to the
simulated provider (e2e/fake-openrouter.py) so no paid request is made. The script refuses to run the analysis
check unless LIVE_OPENROUTER_SIMULATED=1 is set, to make that choice explicit.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"Missing {name}. See docs/PHASE2_AUTH_LIVE.md.")
    return value


def request(method: str, url: str, *, headers: dict | None = None, body: dict | None = None) -> tuple[int, dict | str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:  # noqa: S310 (operator-provided URL)
            raw = response.read().decode("utf-8") or "{}"
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8") or "{}"
        status = error.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def decode_header(token: str) -> dict:
    header = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))


def main() -> int:
    supabase = env("SUPABASE_URL").rstrip("/")
    publishable = env("SUPABASE_PUBLISHABLE_KEY")
    email = env("LIVE_TEST_EMAIL")
    password = env("LIVE_TEST_PASSWORD")
    organization = env("LIVE_ORGANIZATION_SLUG")
    fastapi = env("LIVE_FASTAPI_URL", required=False) or "http://127.0.0.1:8000"
    apikey = {"apikey": publishable}

    loopback_allowed = os.environ.get("LIVE_ALLOW_LOOPBACK") == "1"  # only to validate this script against the simulator
    if not supabase.startswith("https://") and not (loopback_allowed and supabase.startswith("http://127.0.0.1")):
        sys.exit("SUPABASE_URL must be https for a real project.")

    # 1. Public keys: asymmetric algorithms only, no shared secret exposed.
    status, jwks = request("GET", f"{supabase}/auth/v1/.well-known/jwks.json", headers=apikey)
    keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
    algorithms = sorted({key.get("alg", "") for key in keys})
    check("JWKS publishes asymmetric signing keys", status == 200 and bool(keys) and set(algorithms) <= {"ES256", "RS256"},
          f"HTTP {status}, algorithms={algorithms or 'none'}")

    # 2. Real sign-in with the test user.
    status, session = request("POST", f"{supabase}/auth/v1/token?grant_type=password", headers=apikey,
                              body={"email": email, "password": password})
    token = session.get("access_token", "") if isinstance(session, dict) else ""
    refresh = session.get("refresh_token", "") if isinstance(session, dict) else ""
    check("Sign-in with email and password", status == 200 and bool(token), f"HTTP {status}")
    if not token:
        return summarize()
    check("Access token is asymmetric (kid and ES256/RS256)", decode_header(token).get("alg") in {"ES256", "RS256"} and bool(decode_header(token).get("kid")))

    auth = {"Authorization": f"Bearer {token}"}

    # 3. Identity and membership in FastAPI.
    status, me = request("GET", f"{fastapi}/api/v1/me", headers=auth)
    orgs = me.get("organizations", []) if isinstance(me, dict) else []
    analyst_org = next((org for org in orgs if org.get("slug") == organization), None)
    check("GET /api/v1/me accepts the real token", status == 200, f"HTTP {status}")
    check("Membership in the expected organization with role analyst",
          bool(analyst_org) and analyst_org.get("role") == "analyst",
          f"organizations={len(orgs)}")
    if not analyst_org:
        return summarize()
    org_id = analyst_org["id"]

    # 4. Rejections.
    status, _ = request("GET", f"{fastapi}/api/v1/me")
    check("Request without token is rejected (401)", status == 401, f"HTTP {status}")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    status, _ = request("GET", f"{fastapi}/api/v1/me", headers={"Authorization": f"Bearer {tampered}"})
    check("Tampered token is rejected (401)", status == 401, f"HTTP {status}")
    status, _ = request("POST", f"{fastapi}/api/v1/organizations/00000000-0000-4000-8000-000000000000/campaigns/analyze",
                        headers=auth, body={"rawInput": "x"})
    check("Organization without membership is not visible (404)", status == 404, f"HTTP {status}")

    # 5. Analysis through FastAPI with the simulated provider.
    if os.environ.get("LIVE_OPENROUTER_SIMULATED") == "1":
        status, result = request("POST", f"{fastapi}/api/v1/organizations/{org_id}/campaigns/analyze", headers=auth,
                                 body={"rawInput": "Campaña: Prueba en vivo\nInversión: 1000\nClics: 100\nImpresiones: 2000",
                                       "investment": 1000, "clicks": 100, "impressions": 2000})
        check("Analyst generates analysis through FastAPI (simulated provider)",
              status == 200 and isinstance(result, dict) and result.get("analysis", "").startswith("## Estado general"),
              f"HTTP {status}")
    else:
        check("Analysis step skipped", True, "set LIVE_OPENROUTER_SIMULATED=1 with a simulated provider to run it")

    # 6. client_viewer must not generate paid analysis.
    viewer_email = env("LIVE_VIEWER_EMAIL", required=False)
    if viewer_email:
        viewer_password = env("LIVE_VIEWER_PASSWORD")
        status, viewer_session = request("POST", f"{supabase}/auth/v1/token?grant_type=password", headers=apikey,
                                         body={"email": viewer_email, "password": viewer_password})
        viewer_token = viewer_session.get("access_token", "") if isinstance(viewer_session, dict) else ""
        if viewer_token:
            status, _ = request("POST", f"{fastapi}/api/v1/organizations/{org_id}/campaigns/analyze",
                                headers={"Authorization": f"Bearer {viewer_token}"}, body={"rawInput": "x"})
            check("client_viewer is refused paid analysis (403)", status == 403, f"HTTP {status}")
        else:
            check("client_viewer sign-in", False, f"HTTP {status}")

    # 7. Renewal, then sign-out.
    if refresh:
        status, renewed = request("POST", f"{supabase}/auth/v1/token?grant_type=refresh_token", headers=apikey,
                                  body={"refresh_token": refresh})
        check("Session renewal with refresh token", status == 200 and bool(renewed.get("access_token")), f"HTTP {status}")
    status, _ = request("POST", f"{supabase}/auth/v1/logout", headers={**apikey, **auth}, body={})
    check("Sign-out ends the session at the provider", status in (204, 200), f"HTTP {status}")
    status, _ = request("GET", f"{supabase}/auth/v1/user", headers={**apikey, **auth})
    check("Token is rejected by the provider after sign-out", status in (401, 403), f"HTTP {status}")
    print("Note: FastAPI verifies tokens statelessly; a token stays acceptable there until its expiry (see docs).")

    return summarize()


def summarize() -> int:
    failed = [name for name, passed, _ in RESULTS if not passed]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
