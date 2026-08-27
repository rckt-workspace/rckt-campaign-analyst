/**
 * Normalización de métricas de campaña.
 * Solo existe en memoria durante la sesión: sin persistencia, sin backend.
 */

export type CampaignFormFields = {
  campaign: string;
  channel: string;
  objective: string;
  period: string;
  investment: string;
  impressions: string;
  reach: string;
  frequency: string;
  clicks: string;
  ctr: string;
  cpc: string;
  leads: string;
  cpl: string;
  conversions: string;
  revenue: string;
  roas: string;
  additionalContext: string;
};

export type NormalizedCampaign = {
  campaign: string | null;
  channel: string | null;
  objective: string | null;
  period: string | null;
  investment: number | null;
  impressions: number | null;
  reach: number | null;
  frequency: number | null;
  clicks: number | null;
  ctr: number | null;
  cpc: number | null;
  leads: number | null;
  cpl: number | null;
  conversions: number | null;
  revenue: number | null;
  roas: number | null;
  additionalContext: string | null;
  rawInput: string;
  /** Valores del periodo anterior, cuando el usuario los suministra. */
  previous: PreviousMetrics;
};

export type MetricKey =
  | "investment"
  | "impressions"
  | "reach"
  | "frequency"
  | "clicks"
  | "ctr"
  | "cpc"
  | "leads"
  | "cpl"
  | "conversions"
  | "revenue"
  | "roas";

export type PreviousMetrics = Partial<Record<MetricKey, number | undefined>>;

export const EMPTY_FORM: CampaignFormFields = {
  campaign: "",
  channel: "",
  objective: "",
  period: "",
  investment: "",
  impressions: "",
  reach: "",
  frequency: "",
  clicks: "",
  ctr: "",
  cpc: "",
  leads: "",
  cpl: "",
  conversions: "",
  revenue: "",
  roas: "",
  additionalContext: "",
};

export const EXAMPLE_RAW = `Campaña: Lanzamiento RCKT Growth
Canal: Meta Ads
Objetivo: Ventas
Periodo actual: 1 al 15 de agosto de 2026

Inversión: $4.850.000 COP
Impresiones: 410.000
Alcance: 245.000
Frecuencia: 1,67
Clics: 8.610
CTR: 2,10%
CPC: $563
Leads: 522
CPL: $9.291
Ventas: 94
Ingresos atribuidos: $11.960.000 COP
ROAS: 2,47

Periodo anterior:
CTR: 1,65%
CPC: $720
CPL: $12.500
ROAS: 1,95

Información adicional:
El cliente quiere aumentar ventas sin deteriorar demasiado el costo de adquisición.
No existen todavía resultados separados por anuncio, audiencia o ubicación.`;

export const EXAMPLE_FORM: CampaignFormFields = {
  campaign: "Lanzamiento RCKT Growth",
  channel: "Meta Ads",
  objective: "Ventas",
  period: "1 al 15 de agosto de 2026",
  investment: "$4.850.000 COP",
  impressions: "410.000",
  reach: "245.000",
  frequency: "1,67",
  clicks: "8.610",
  ctr: "2,10%",
  cpc: "$563",
  leads: "522",
  cpl: "$9.291",
  conversions: "94",
  revenue: "$11.960.000 COP",
  roas: "2,47",
  additionalContext:
    "El cliente quiere aumentar ventas sin deteriorar demasiado el costo de adquisición. No existen todavía resultados separados por anuncio, audiencia o ubicación.\n\nPeriodo anterior: CTR 1,65% · CPC $720 · CPL $12.500 · ROAS 1,95",
};

/**
 * Convierte formatos latinos y anglosajones a número.
 * "$4.850.000 COP" -> 4850000 · "2,47" -> 2.47 · "2.10%" -> 2.1 · "1,234.56" -> 1234.56
 */
export function parseLocaleNumber(input: string): number | null {
  if (typeof input !== "string") return null;
  const cleaned = input
    .replace(/%/g, "")
    .replace(/[^\d.,-]/g, "")
    .trim();
  if (!cleaned || !/\d/.test(cleaned)) return null;

  const negative = cleaned.startsWith("-");
  let body = cleaned.replace(/-/g, "");

  const lastComma = body.lastIndexOf(",");
  const lastDot = body.lastIndexOf(".");

  const groupsOfThree = (value: string, sep: string) =>
    new RegExp(`^\\d{1,3}(\\${sep}\\d{3})+$`).test(value);

  if (lastComma !== -1 && lastDot !== -1) {
    // El separador más a la derecha es el decimal.
    if (lastComma > lastDot) {
      body = body.replace(/\./g, "").replace(",", ".");
    } else {
      body = body.replace(/,/g, "");
    }
  } else if (lastComma !== -1) {
    const decimals = body.length - lastComma - 1;
    if (groupsOfThree(body, ",")) {
      body = body.replace(/,/g, ""); // 1,234 / 4,850,000 -> miles
    } else if (decimals > 0 && decimals <= 2) {
      body = body.replace(",", "."); // 2,47 -> decimal
    } else {
      body = body.replace(/,/g, "");
    }
  } else if (lastDot !== -1) {
    const decimals = body.length - lastDot - 1;
    if (groupsOfThree(body, ".")) {
      body = body.replace(/\./g, ""); // 4.850.000 -> miles
    } else if (decimals === 3 && body.split(".").length === 2) {
      // Ambiguo (ej. 4.850): en formato latino suele ser miles.
      body = body.replace(/\./g, "");
    } else {
      body = body.replace(/,/g, "");
    }
  }

  const parsed = Number(body);
  if (!Number.isFinite(parsed)) return null;
  return negative ? -parsed : parsed;
}

const trimOrNull = (value: string) => {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
};

const numOrNull = (value: string) => (value.trim() ? parseLocaleNumber(value) : null);

const LABELS: Record<Exclude<keyof NormalizedCampaign, "rawInput" | "previous">, string[]> = {
  campaign: ["campaña", "campana", "campaign", "nombre de campaña"],
  channel: ["canal", "channel", "plataforma"],
  objective: ["objetivo", "objective", "meta"],
  period: ["periodo actual", "período actual", "periodo", "período", "fechas", "rango"],
  investment: ["inversión", "inversion", "gasto", "presupuesto", "spend", "investment"],
  impressions: ["impresiones", "impressions"],
  reach: ["alcance", "reach", "personas alcanzadas"],
  frequency: ["frecuencia", "frequency"],
  clicks: ["clics", "clicks", "clics en el enlace"],
  ctr: ["ctr"],
  cpc: ["cpc", "costo por clic"],
  leads: ["leads", "prospectos", "registros"],
  cpl: ["cpl", "costo por lead"],
  conversions: ["ventas", "conversiones", "conversions", "compras", "sales"],
  revenue: ["ingresos atribuidos", "ingresos", "revenue", "valor de conversión", "facturación"],
  roas: ["roas", "retorno"],
  additionalContext: ["información adicional", "informacion adicional", "contexto", "notas"],
};

const TEXT_KEYS = ["campaign", "channel", "objective", "period"] as const;

const PREVIOUS_MARKER = /(periodo|período)\s+(anterior|previo)/i;
const PREVIOUS_SUFFIX = /\b(anterior|previo|previous|pasado)\b/i;

function parseRawInput(raw: string): Partial<NormalizedCampaign> {
  const result: Partial<NormalizedCampaign> = {};
  const previous: PreviousMetrics = {};
  const lines = raw.split(/\r?\n/);
  const extraLines: string[] = [];
  let inAdditional = false;
  let previousBlock = false;

  for (const line of lines) {
    const match = line.match(/^\s*([^:]{1,60}?)\s*:\s*(.*)$/);
    if (!match) {
      if (PREVIOUS_MARKER.test(line)) {
        previousBlock = true;
        inAdditional = false;
        continue;
      }
      if (inAdditional && line.trim()) extraLines.push(line.trim());
      continue;
    }
    const label = (match[1] ?? "").trim().toLowerCase();
    const value = (match[2] ?? "").trim();

    // "Periodo anterior:" abre el bloque histórico.
    if (PREVIOUS_MARKER.test(label)) {
      previousBlock = true;
      inAdditional = false;
      continue;
    }

    const isPreviousMetric = PREVIOUS_SUFFIX.test(label);
    if (isPreviousMetric || previousBlock) {
      const cleanLabel = label.replace(PREVIOUS_SUFFIX, "").replace(/\s+/g, " ").trim();
      const key = matchMetricKey(cleanLabel);
      if (key) {
        const parsed = parseLocaleNumber(value);
        if (parsed !== null && previous[key] === undefined) previous[key] = parsed;
        inAdditional = false;
        continue;
      }
      if (isPreviousMetric) continue;
    }

    if (LABELS.additionalContext.includes(label)) {
      inAdditional = true;
      if (value) extraLines.push(value);
      continue;
    }

    let matchedKey: string | null = null;
    for (const [key, aliases] of Object.entries(LABELS)) {
      if (key === "additionalContext") continue;
      if (aliases.some((alias) => label === alias || label.startsWith(`${alias} `))) {
        matchedKey = key;
        break;
      }
    }
    if (!matchedKey) {
      if (inAdditional && line.trim()) extraLines.push(line.trim());
      continue;
    }
    // Solo el primer valor encontrado (periodo actual antes que periodo anterior).
    if ((result as Record<string, unknown>)[matchedKey] != null) continue;

    if ((TEXT_KEYS as readonly string[]).includes(matchedKey)) {
      (result as Record<string, unknown>)[matchedKey] = value || null;
    } else {
      const parsed = parseLocaleNumber(value);
      if (parsed !== null) (result as Record<string, unknown>)[matchedKey] = parsed;
    }
    inAdditional = false;
  }

  if (extraLines.length) result.additionalContext = extraLines.join("\n");
  if (Object.keys(previous).length) result.previous = previous;
  return result;
}

const METRIC_KEYS = [
  "investment",
  "impressions",
  "reach",
  "frequency",
  "clicks",
  "ctr",
  "cpc",
  "leads",
  "cpl",
  "conversions",
  "revenue",
  "roas",
] as const;

function matchMetricKey(label: string): MetricKey | null {
  for (const key of METRIC_KEYS) {
    if (LABELS[key].some((alias) => label === alias || label.startsWith(`${alias} `))) return key;
  }
  return null;
}

export function normalizeCampaign(
  mode: "paste" | "form",
  raw: string,
  form: CampaignFormFields,
): NormalizedCampaign {
  const base: NormalizedCampaign = {
    campaign: null,
    channel: null,
    objective: null,
    period: null,
    investment: null,
    impressions: null,
    reach: null,
    frequency: null,
    clicks: null,
    ctr: null,
    cpc: null,
    leads: null,
    cpl: null,
    conversions: null,
    revenue: null,
    roas: null,
    additionalContext: null,
    rawInput: "",
    previous: {},
  };

  if (mode === "paste") {
    return { ...base, ...parseRawInput(raw), rawInput: raw.trim() };
  }

  const rawFromForm = Object.entries(form)
    .filter(([, value]) => value.trim())
    .map(([key, value]) => `${key}: ${value.trim()}`)
    .join("\n");

  return {
    ...base,
    campaign: trimOrNull(form.campaign),
    channel: trimOrNull(form.channel),
    objective: trimOrNull(form.objective),
    period: trimOrNull(form.period),
    investment: numOrNull(form.investment),
    impressions: numOrNull(form.impressions),
    reach: numOrNull(form.reach),
    frequency: numOrNull(form.frequency),
    clicks: numOrNull(form.clicks),
    ctr: numOrNull(form.ctr),
    cpc: numOrNull(form.cpc),
    leads: numOrNull(form.leads),
    cpl: numOrNull(form.cpl),
    conversions: numOrNull(form.conversions),
    revenue: numOrNull(form.revenue),
    roas: numOrNull(form.roas),
    additionalContext: trimOrNull(form.additionalContext),
    rawInput: rawFromForm,
  };
}

/** Cantidad de campos con información útil (excluye rawInput). */
export function countFilledFields(data: NormalizedCampaign): number {
  return Object.entries(data).filter(
    ([key, value]) => key !== "rawInput" && key !== "previous" && value !== null && value !== "",
  ).length;
}

export function isEmptyInput(mode: "paste" | "form", raw: string, form: CampaignFormFields) {
  if (mode === "paste") return raw.trim().length === 0;
  return Object.values(form).every((value) => value.trim().length === 0);
}
