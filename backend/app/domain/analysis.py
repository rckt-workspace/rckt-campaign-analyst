"""Analysis history vocabulary: states, call steps, model roles and the error codes that may be stored.

Pure constants. The database enforces the same lists (CHECK constraints), so changing one requires a migration.
Error codes are fixed words: provider bodies, exception text and URLs are never stored. The HTTP status of a
provider response is stored as a number instead.
"""

ANALYSIS_STATUSES = ("running", "succeeded", "failed")
ATTEMPT_STATUSES = ("reserved", "succeeded", "failed", "unknown")

# Step: why the HTTP call is made. "format_retry" repeats the request after an answer that broke the required format.
ATTEMPT_STEPS = ("initial", "format_retry")
# Model role: which configured model the call uses. "fallback" only follows a failed "primary" in the same step.
MODEL_ROLES = ("primary", "fallback")

# Each step makes at most two calls (primary, then fallback), and a request makes at most two steps: four calls.
MAX_ATTEMPTS_PER_ANALYSIS = 4

ABANDONED = "ANALYSIS_ABANDONED"

SAFE_ERROR_CODES = (
    "AI_TIMEOUT",  # the provider did not answer within CHAT_TIMEOUT_MS
    "AI_NETWORK",  # transport failure before a response
    "AI_EMPTY",  # a response without usable text
    "AI_HTTP_ERROR",  # a non-success HTTP status; the number is stored in http_status
    "AI_INVALID_RESPONSE",  # the answer does not follow the required format after the allowed retry
    "AI_NOT_CONFIGURED",  # missing server configuration; no call was made
    ABANDONED,  # the lease expired while the analysis was running; the owner's result is discarded
    "INTERNAL_ERROR",
)

MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_ANALYSIS_MARKDOWN_LENGTH = 100_000


def quoted_list(values: tuple[str, ...]) -> str:
    """Renders ('a', 'b') as the SQL literal list "'a', 'b'" used in CHECK constraints."""
    return ", ".join(f"'{value}'" for value in values)
