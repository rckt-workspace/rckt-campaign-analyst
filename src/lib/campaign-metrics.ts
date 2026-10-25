/**
 * Normalización de métricas de campaña.
 * Solo existe en memoria durante la sesión: sin persistencia, sin backend.
 *
 * Los números se leen con una interpretación estricta (ver metric-number.ts). Un valor ambiguo o inválido no se
 * convierte ni se sustituye: queda como `null` en el campo y registrado en `issues`, y la interfaz impide analizar
 * o guardar mientras existan incidencias.
 */

import {
  describeMetricIssue,
  readMetricNumber,
  type MetricIssueReason,
  type MetricKind,
} from "./metric-number";

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

/** A number the user wrote that could not be read safely. The original text is kept for the message. */
export type MetricIssue = {
  /** Campo afectado: "investment" o "previous.ctr" para el periodo anterior. */
  field: string;
  /** Nombre visible del campo en el aviso. */
  label: string;
  /** Texto original, tal como lo escribió el usuario. */
  raw: string;
  reason: MetricIssueReason;
  kind: MetricKind;
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
  /** Valores que no pudieron leerse con seguridad. Si hay alguno, no se analiza ni se guarda. */
  issues: MetricIssue[];
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

/** Tipo de precisión de cada métrica, igual que la columna que la guarda en PostgreSQL. */
export const METRIC_KIND: Record<MetricKey, MetricKind> = {
  investment: "money",
  revenue: "money",
  cpc: "unit_cost",
  cpl: "unit_cost",
  frequency: "ratio",
  roas: "ratio",
  ctr: "percent",
  impressions: "count",
  reach: "count",
  clicks: "count",
  leads: "count",
  conversions: "count",
};

export const METRIC_LABEL: Record<MetricKey, string> = {
  investment: "Inversión",
  impressions: "Impresiones",
  reach: "Alcance",
  frequency: "Frecuencia",
  clicks: "Clics",
  ctr: "CTR",
  cpc: "CPC",
  leads: "Leads",
  cpl: "CPL",
  conversions: "Ventas",
  revenue: "Ingresos atribuidos",
  roas: "ROAS",
};

/** Aviso completo para un campo: nombre, texto original y motivo. Nunca muestra un valor de reemplazo. */
export function describeIssue(issue: MetricIssue): string {
  return `${issue.label}: «${issue.raw}». ${describeMetricIssue(issue.reason, issue.kind)}`;
}

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

const trimOrNull = (value: string) => {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
};

/**
 * Lee una métrica numérica. Devuelve el valor, o null si está vacía o si es inválida; en este último caso registra
 * la incidencia con el texto original.
 */
function readMetric(
  key: MetricKey,
  raw: string,
  issues: MetricIssue[],
  previous = false,
): number | null {
  const reading = readMetricNumber(raw, METRIC_KIND[key]);
  if (reading.status === "ok") return reading.value;
  if (reading.status === "invalid") {
    issues.push({
      field: previous ? `previous.${key}` : key,
      label: previous ? `${METRIC_LABEL[key]} (periodo anterior)` : METRIC_LABEL[key],
      raw: raw.trim(),
      reason: reading.reason,
      kind: METRIC_KIND[key],
    });
  }
  return null;
}

const LABELS: Record<
  Exclude<keyof NormalizedCampaign, "rawInput" | "previous" | "issues">,
  string[]
> = {
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

function parseRawInput(raw: string, issues: MetricIssue[]): Partial<NormalizedCampaign> {
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
        const parsed = readMetric(key, value, issues, true);
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
      const parsed = readMetric(matchedKey as MetricKey, value, issues);
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
  const issues: MetricIssue[] = [];
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
    issues,
  };

  if (mode === "paste") {
    return { ...base, ...parseRawInput(raw, issues), rawInput: raw.trim(), issues };
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
    investment: readMetric("investment", form.investment, issues),
    impressions: readMetric("impressions", form.impressions, issues),
    reach: readMetric("reach", form.reach, issues),
    frequency: readMetric("frequency", form.frequency, issues),
    clicks: readMetric("clicks", form.clicks, issues),
    ctr: readMetric("ctr", form.ctr, issues),
    cpc: readMetric("cpc", form.cpc, issues),
    leads: readMetric("leads", form.leads, issues),
    cpl: readMetric("cpl", form.cpl, issues),
    conversions: readMetric("conversions", form.conversions, issues),
    revenue: readMetric("revenue", form.revenue, issues),
    roas: readMetric("roas", form.roas, issues),
    additionalContext: trimOrNull(form.additionalContext),
    rawInput: rawFromForm,
    issues,
  };
}

/** Cantidad de campos con información útil (excluye rawInput, previous e issues). */
export function countFilledFields(data: NormalizedCampaign): number {
  return Object.entries(data).filter(
    ([key, value]) =>
      key !== "rawInput" &&
      key !== "previous" &&
      key !== "issues" &&
      value !== null &&
      value !== "",
  ).length;
}

export function isEmptyInput(mode: "paste" | "form", raw: string, form: CampaignFormFields) {
  if (mode === "paste") return raw.trim().length === 0;
  return Object.values(form).every((value) => value.trim().length === 0);
}
