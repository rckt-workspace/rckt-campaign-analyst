"""RCKT Campaign Analyst FastAPI service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.api.routes import router
from app.core.config import get_database_settings
from app.db.session import Database, DatabaseUnavailableError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The engine connects lazily, so startup never waits for the database. Tables are not created here.
    settings = get_database_settings()
    app.state.database = Database(settings) if settings else None
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
