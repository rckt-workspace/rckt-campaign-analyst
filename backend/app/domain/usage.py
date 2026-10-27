"""Provider usage of one HTTP call, and the money rules applied to it. Pure: no HTTP, no database.

Decisions (docs/PHASE2_PROVIDER_USAGE.md):
- Every value is exact. Money is Decimal, never float. A missing value is None, never zero.
- An explicit zero (the provider reported 0) is stored as 0. Only an absent or invalid value is None.
- cost_micros (micro-USD) is filled only once the unit of OpenRouter's cost is confirmed. Until then the exact
  reported number is kept in provider_cost and cost_micros stays None. usd_to_micros defines the conversion now,
  so the rule is tested before anything depends on it.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Column limits: analysis_usage.prompt_tokens / completion_tokens are INTEGER; provider_cost is NUMERIC(18, 8).
MAX_TOKEN_COUNT = 2_147_483_647
MAX_PROVIDER_COST = Decimal("9999999999.99999999")
MAX_MICROS = 9_223_372_036_854_775_807  # BIGINT maximum (cost_micros is BIGINT); MAX_PROVIDER_COST * 10^6 fits well below it


@dataclass(frozen=True)
class ProviderUsage:
    """Usage the provider actually reported for one call. Absent fields stay None; nothing is estimated."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    provider_cost: Decimal | None = None
    cost_micros: int | None = None

    @property
    def has_any_value(self) -> bool:
        return any(value is not None for value in (
            self.prompt_tokens, self.completion_tokens, self.provider_cost, self.cost_micros,
        ))


def usd_to_micros(amount: Decimal) -> int:
    """Converts an exact amount to micro-USD: multiply by 1,000,000 and round half up to a whole micro.

    Rounding is half-up on purpose: 0.0000005 is 1 micro, 0.0000004 is 0. Negative, non-finite or out-of-range
    amounts are refused, never clamped.
    """
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise ValueError("amount must be a finite Decimal")
    if amount < 0 or amount > MAX_PROVIDER_COST:
        raise ValueError("amount is outside the accepted range")
    try:
        micros = (amount * Decimal(1_000_000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:  # pragma: no cover - defensive; quantize cannot fail for bounded inputs
        raise ValueError("amount cannot be converted") from exc
    if micros > MAX_MICROS:
        raise ValueError("amount is outside the accepted range")
    return int(micros)
