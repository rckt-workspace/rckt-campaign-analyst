/**
 * Strict reading of numbers typed by a user, in Colombian or international format.
 *
 * Rules, in short:
 * - Never guesses. A value that could mean two different numbers is reported as "ambiguous", not converted.
 * - Two separators: the last one is the decimal mark, the other one must group thousands in blocks of three.
 * - One separator: followed by exactly three digits it is ambiguous ("1234,567", "4.850"); otherwise it is decimal.
 * - Each kind of metric has the precision of the PostgreSQL column that stores it (backend migration 0003_campaigns).
 * - A value is returned only when it is exact in JavaScript: converting it back to text gives the same digits.
 *
 * Pure functions. No I/O, no framework imports.
 */

export type MetricKind = "money" | "unit_cost" | "ratio" | "percent" | "count";

/** Integer digits and decimals each kind can hold. Mirrors numeric(14,2), numeric(14,4), numeric(12,4), numeric(9,4). */
export const METRIC_LIMITS: Record<MetricKind, { integerDigits: number; decimals: number }> = {
  money: { integerDigits: 12, decimals: 2 },
  unit_cost: { integerDigits: 10, decimals: 4 },
  ratio: { integerDigits: 8, decimals: 4 },
  percent: { integerDigits: 5, decimals: 4 },
  // bigint in the database; limited to 15 digits so every count is an exact JavaScript integer.
  count: { integerDigits: 15, decimals: 0 },
};

export type MetricIssueReason =
  "malformed" | "ambiguous" | "negative" | "too_many_decimals" | "too_large" | "fractional_count";

export type MetricReading =
  | { status: "empty" }
  | { status: "ok"; value: number }
  | { status: "invalid"; reason: MetricIssueReason };

const CURRENCY = /\$|€|\b(COP|USD|EUR)\b/gi;
const ALLOWED_BODY = /^\d+([.,]\d+)*$/;

/**
 * Splits "1.234,56" into integer digits and fraction digits. Returns a reason when the grouping is not valid.
 * `pesos` is true when the text carries a peso marker ("$" or "COP"): in Colombian pesos "." groups thousands.
 */
function splitDigits(
  body: string,
  kind: MetricKind,
  pesos: boolean,
): { integer: string; fraction: string } | { reason: MetricIssueReason } {
  const hasDot = body.includes(".");
  const hasComma = body.includes(",");

  if (hasDot && hasComma) {
    const lastDot = body.lastIndexOf(".");
    const lastComma = body.lastIndexOf(",");
    const decimalMark = lastDot > lastComma ? "." : ",";
    const thousandsMark = decimalMark === "." ? "," : ".";
    const cut = Math.max(lastDot, lastComma);
    const integerPart = body.slice(0, cut);
    if (!isGrouped(integerPart, thousandsMark)) return { reason: "malformed" };
    return { integer: integerPart.split(thousandsMark).join(""), fraction: body.slice(cut + 1) };
  }

  if (!hasDot && !hasComma) return { integer: body, fraction: "" };

  const mark = hasDot ? "." : ",";
  const parts = body.split(mark);
  if (parts.length > 2) {
    // "4.850.000": only thousands grouping can repeat the same mark.
    if (!isGrouped(body, mark)) return { reason: "malformed" };
    return { integer: parts.join(""), fraction: "" };
  }

  const [integerPart = "", fraction = ""] = parts;
  if (fraction.length === 3) {
    // Exactly three digits after a dot. Thousands is the only possible reading in two cases:
    // - counts never hold decimals, so "8.610" can only be 8610;
    // - a peso amount ("$9.291", "COP 12.500") is written with dots for thousands in Colombia.
    // Any other case (a comma, or a dot without peso context) could mean two different numbers: it is refused.
    const thousandsIsOnlyReading = kind === "count" || (pesos && mark === ".");
    if (thousandsIsOnlyReading && integerPart.length >= 1 && integerPart.length <= 3) {
      return { integer: integerPart + fraction, fraction: "" };
    }
    return { reason: "ambiguous" };
  }
  return { integer: integerPart, fraction };
}

/** True when every group after the first has exactly three digits, and the first has one to three. */
function isGrouped(text: string, mark: string): boolean {
  const [first = "", ...rest] = text.split(mark);
  return first.length >= 1 && first.length <= 3 && rest.every((group) => group.length === 3);
}

/**
 * Reads a single user-typed number. Currency symbols, currency codes and percent signs are ignored, because they
 * carry no digits. Everything else must be digits, at most one of "." or "," as separators, and an optional leading "-".
 */
export function readMetricNumber(input: string, kind: MetricKind): MetricReading {
  const trimmed = input.trim();
  if (!trimmed) return { status: "empty" };

  const pesos = /\$|\bCOP\b/i.test(trimmed);
  const body = trimmed.replace(CURRENCY, "").replace(/%/g, "").trim();
  const negative = body.startsWith("-");
  const unsigned = negative ? body.slice(1) : body;
  if (!ALLOWED_BODY.test(unsigned)) return { status: "invalid", reason: "malformed" };

  const split = splitDigits(unsigned, kind, pesos);
  if ("reason" in split) return { status: "invalid", reason: split.reason };

  // Trailing zeros carry no information: "2,10" is 2.1 with one decimal. Leading zeros are dropped.
  const integer = split.integer.replace(/^0+(?=\d)/, "");
  const fraction = split.fraction.replace(/0+$/, "");
  const limits = METRIC_LIMITS[kind];

  if (kind === "count" && fraction.length > 0)
    return { status: "invalid", reason: "fractional_count" };
  if (fraction.length > limits.decimals) return { status: "invalid", reason: "too_many_decimals" };
  if (integer.length > limits.integerDigits) return { status: "invalid", reason: "too_large" };

  const canonical = fraction ? `${integer}.${fraction}` : integer;
  const value = Number(canonical);
  // The number must print back to the same digits. Anything else would silently change the amount.
  if (!Number.isFinite(value) || String(value) !== canonical)
    return { status: "invalid", reason: "too_large" };

  if (negative) return { status: "invalid", reason: "negative" };
  return { status: "ok", value };
}

const KIND_NAMES: Record<MetricKind, string> = {
  money: "importe",
  unit_cost: "costo unitario",
  ratio: "ratio",
  percent: "porcentaje",
  count: "conteo",
};

/** Explanation shown to the user for an invalid reading. Says what to change; never shows a replacement value. */
export function describeMetricIssue(reason: MetricIssueReason, kind: MetricKind): string {
  const limits = METRIC_LIMITS[kind];
  switch (reason) {
    case "malformed":
      return "No se reconoce como número. Escríbelo solo con cifras y separadores, por ejemplo 4.850.000 o 2,47.";
    case "ambiguous":
      return "Separador ambiguo. Revisa si la coma o el punto separa miles o decimales: escribe 1234,567 como 1234.567 o 1.234.567 según corresponda.";
    case "negative":
      return "Los valores negativos no son válidos para esta métrica.";
    case "too_many_decimals":
      return kind === "count"
        ? "Este conteo no admite decimales."
        : `Este ${KIND_NAMES[kind]} admite hasta ${limits.decimals} decimales. Corrige la cifra; no se redondea.`;
    case "too_large":
      return `Supera el máximo de ${limits.integerDigits} cifras enteras para este ${KIND_NAMES[kind]}.`;
    case "fractional_count":
      return "Un conteo debe ser un número entero.";
  }
}
