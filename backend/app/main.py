"""RCKT Campaign Analyst FastAPI service."""

from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="RCKT Campaign Analyst API",
    version="1.0.0",
    description="Internal API for the single-agent campaign analysis workflow.",
)
app.include_router(router)


@app.get("/healthz", include_in_schema=False)
def health() -> dict[str, str]:
    # Liveness intentionally has no external dependencies and consumes no LLM tokens.
    return {"status": "ok"}
