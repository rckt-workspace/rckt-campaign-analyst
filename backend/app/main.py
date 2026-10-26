"""RCKT Campaign Analyst FastAPI service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.campaign_routes import router as campaign_router
from app.api.analysis_routes import router as analysis_router
from app.api.identity import router as identity_router
from app.api.routes import router
from app.core.config import get_auth_settings, get_database_settings
from app.db.session import Database, DatabaseUnavailableError
from app.security.tokens import SupabaseTokenVerifier

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The engine connects lazily, so startup never waits for the database. Tables are not created here.
    settings = get_database_settings()
    app.state.database = Database(settings) if settings else None
    auth = get_auth_settings()
    if auth is None:
        # Logged without any value. Protected routes will answer 503 until SUPABASE_URL is configured.
        logger.warning("SUPABASE_URL is not set; authenticated routes are disabled")
        app.state.token_verifier = None
    else:
        app.state.token_verifier = SupabaseTokenVerifier.from_jwks_url(
            jwks_url=auth.jwks_url, issuer=auth.issuer, audience=auth.audience
        )
    try:
        yield
    finally:
        if app.state.database is not None:
            await app.state.database.dispose()


app = FastAPI(
    title="RCKT Campaign Analyst API",
    version="1.0.0",
    description="Internal API for the single-agent campaign analysis workflow.",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(identity_router)
app.include_router(campaign_router)
app.include_router(analysis_router)


@app.exception_handler(DatabaseUnavailableError)
async def database_unavailable(_: Request, __: DatabaseUnavailableError) -> JSONResponse:
    logger.error("database unavailable during request")
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


@app.get("/healthz", include_in_schema=False)
def health() -> dict[str, str]:
    # Liveness intentionally has no external dependencies and consumes no LLM tokens.
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def ready() -> dict[str, str]:
    database: Database | None = getattr(app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="database not configured")
    try:
        await database.check_connection()
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="database unavailable") from None
    return {"status": "ready"}
