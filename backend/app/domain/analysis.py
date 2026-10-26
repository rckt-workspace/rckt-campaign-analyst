"""Analysis history vocabulary: states, attempt kinds and the error codes that may be stored.

Pure constants. The database enforces the same lists (CHECK constraints), so changing one requires a migration.
Error codes are fixed words: provider bodies, exception text and URLs are never stored.
"""

ANALYSIS_STATUSES = ("running", "succeeded", "failed")
ATTEMPT_STATUSES = ("reserved", "succeeded", "failed")
ATTEMPT_KINDS = ("primary", "fallback")

SAFE_ERROR_CODES = (
    "AI_TIMEOUT",
    "AI_UNAVAILABLE",
    "AI_INVALID_RESPONSE",
    "AI_NOT_CONFIGURED",
    "ANALYSIS_ABANDONED",
    "INTERNAL_ERROR",
)

MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_ANALYSIS_MARKDOWN_LENGTH = 100_000


def quoted_list(values: tuple[str, ...]) -> str:
    """Renders ('a', 'b') as the SQL literal list "'a', 'b'" used in CHECK constraints."""
    return ", ".join(f"'{value}'" for value in values)
