/**
 * Convierte la respuesta markdown del agente en una estructura lógica
 * únicamente para facilitar el renderizado del informe. No persiste nada.
 */

export type ReportStatus = "good" | "attention" | "critical" | "insufficient" | "unknown";
export type MetricDirection = "up" | "down" | "flat" | null;
export type Priority = "high" | "medium" | "low" | null;

export type Finding = {
  title: string;
  description: string;
  keyMetric: string | null;
  direction: MetricDirection;
};

export type Recommendation = {
  title: string;
  description: string;
  priority: Priority;
};

export type CampaignReport = {
  status: ReportStatus;
  statusLabel: string;
  statusExplanation: string;
  executiveSummary: string;
  findings: Finding[];
  recommendations: Recommendation[];
  missingInformation: string[];
  dataWarnings: string[];
  /** Texto original, usado como fallback cuando no se reconoce la estructura. */
  raw: string;
  structured: boolean;
};

const STATUS_LABELS: Record<Exclude<ReportStatus, "unknown">, string> = {
  good: "Buen desempeño",
  attention: "Requiere atención",
  critical: "Desempeño crítico",
  insufficient: "Información insuficiente",
};

function normalizeHeading(value: string) {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z ]/g, "")
    .trim();
}

function stripMarkdown(value: string) {
  return value
    .replace(/\*\*/g, "")
    .replace(/^#+\s*/, "")
    .trim();
}

function detectStatus(text: string): { status: ReportStatus; label: string } {
  if (text.includes("🟢")) return { status: "good", label: STATUS_LABELS.good };
  if (text.includes("🟡")) return { status: "attention", label: STATUS_LABELS.attention };
  if (text.includes("🔴")) return { status: "critical", label: STATUS_LABELS.critical };
  if (text.includes("⚪") || text.includes("⚫"))
    return { status: "insufficient", label: STATUS_LABELS.insufficient };

  const plain = normalizeHeading(text);
  if (plain.includes("informacion insuficiente"))
    return { status: "insufficient", label: STATUS_LABELS.insufficient };
  if (plain.includes("critico")) return { status: "critical", label: STATUS_LABELS.critical };
  if (plain.includes("requiere atencion"))
    return { status: "attention", label: STATUS_LABELS.attention };
  if (plain.includes("buen desempeno")) return { status: "good", label: STATUS_LABELS.good };
  return { status: "unknown", label: "Análisis disponible" };
}

function detectDirection(value: string | null): MetricDirection {
  if (!value) return null;
  if (/[↑▲]|\bsube\b|\baument/i.test(value)) return "up";
  if (/[↓▼]|\bbaja\b|\bcae\b|\bdisminu/i.test(value)) return "down";
  if (/[→=]|\bestable\b|\bse mantiene\b/i.test(value)) return "flat";
  const signed = value.match(/(^|[\s(])([+-])\s?\d/);
  if (signed) return signed[2] === "+" ? "up" : "down";
  return null;
}

function detectPriority(value: string): Priority {
  const plain = normalizeHeading(value);
  if (/\b(alta|high)\b/.test(plain)) return "high";
  if (/\b(media|medium)\b/.test(plain)) return "medium";
  if (/\b(baja|low)\b/.test(plain)) return "low";
  return null;
}

/** Divide un bloque en ítems numerados o con viñeta. */
function splitItems(block: string): string[] {
  const lines = block.split(/\r?\n/);
  const items: string[] = [];
  let current: string[] = [];

  const isStart = (line: string) => /^\s*(?:\d+[.)]|[-*•])\s+/.test(line);

  for (const line of lines) {
    if (isStart(line)) {
      if (current.length) items.push(current.join("\n"));
      current = [line.replace(/^\s*(?:\d+[.)]|[-*•])\s+/, "")];
    } else if (current.length) {
      current.push(line);
    } else if (line.trim()) {
      current = [line];
    }
  }
  if (current.length) items.push(current.join("\n"));

  return items.map((item) => item.trim()).filter(Boolean);
}

function parseFinding(item: string): Finding {
  const lines = item
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const first = lines.shift() ?? "";

  let title = "";
  let rest = first;
  const bold = first.match(/^\*\*(.+?)\*\*\s*[:—-]?\s*(.*)$/);
  if (bold) {
    title = bold[1]!.trim();
    rest = bold[2] ?? "";
  } else {
    const split = first.split(/\s*[:—–]\s+/);
    title = stripMarkdown(split[0] ?? first);
    rest = split.slice(1).join(": ");
  }

  const body: string[] = [];
  if (rest.trim()) body.push(stripMarkdown(rest));

  let keyMetric: string | null = null;
  for (const line of lines) {
    const key = line.match(/^\**\s*dato\s+clave\s*\**\s*[:：]\s*(.+)$/i);
    if (key) {
      keyMetric = stripMarkdown(key[1]!);
      continue;
    }
    body.push(stripMarkdown(line));
  }

  return {
    title: title || "Hallazgo",
    description: body.join(" ").trim(),
    keyMetric,
    direction: detectDirection(keyMetric),
  };
}

function parseRecommendation(item: string): Recommendation {
  const lines = item
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const first = lines.shift() ?? "";

  let title = "";
  let rest = first;
  const bold = first.match(/^\*\*(.+?)\*\*\s*[:—-]?\s*(.*)$/);
  if (bold) {
    title = bold[1]!.trim();
    rest = bold[2] ?? "";
  } else {
    const split = first.split(/\s*[:—–]\s+/);
    title = stripMarkdown(split[0] ?? first);
    rest = split.slice(1).join(": ");
  }

  const body: string[] = [];
  if (rest.trim()) body.push(stripMarkdown(rest));

  let priority: Priority = null;
  for (const line of lines) {
    const match = line.match(/^\**\s*(?:prioridad|priority)\s*\**\s*[:：]\s*(.+)$/i);
    if (match) {
      priority = detectPriority(match[1]!);
      continue;
    }
    body.push(stripMarkdown(line));
  }

  let description = body.join(" ").trim();
  const inline = description.match(
    /\(?\s*(?:prioridad|priority)\s*[:：]\s*(alta|media|baja|high|medium|low)\s*\)?\.?/i,
  );
  if (inline) {
    priority = priority ?? detectPriority(inline[1]!);
    description = description.replace(inline[0], "").trim();
  }

  return { title: title || "Acción recomendada", description, priority };
}

export function parseCampaignReport(text: string): CampaignReport {
  const raw = (text ?? "").trim();
  const empty: CampaignReport = {
    status: "unknown",
    statusLabel: "Análisis disponible",
    statusExplanation: "",
    executiveSummary: "",
    findings: [],
    recommendations: [],
    missingInformation: [],
    dataWarnings: [],
    raw,
    structured: false,
  };
  if (!raw) return empty;

  const sections = new Map<string, string>();
  const parts = raw.split(/^\s{0,3}#{1,4}\s+(.+)$/gm);
  const preamble = (parts.shift() ?? "").trim();
  for (let i = 0; i < parts.length; i += 2) {
    const heading = normalizeHeading(parts[i] ?? "");
    const body = (parts[i + 1] ?? "").trim();
    if (heading) sections.set(heading, body);
  }

  const find = (needle: string) => {
    for (const [heading, body] of sections) {
      if (heading.includes(needle)) return body;
    }
    return "";
  };

  const statusBlock = find("estado general") || preamble;
  const summary = find("resumen ejecutivo");
  const findingsBlock = find("hallazgos");
  const actionsBlock = find("que haria ahora") || find("recomendaciones");
  const missingBlock = find("informacion que ayudaria") || find("informacion faltante");
  const warningBlock = find("dato a revisar") || find("datos a revisar");

  const statusLines = statusBlock
    .split(/\r?\n/)
    .map((line) => stripMarkdown(line))
    .filter(Boolean);
  const detected = detectStatus(statusLines[0] ?? statusBlock);
  const explanationParts = statusLines.slice(1);
  if (statusLines[0]) {
    const trailing = statusLines[0]
      .replace(/[🟢🟡🔴⚪⚫]/gu, "")
      .replace(
        new RegExp(`^\\s*(?:${Object.values(STATUS_LABELS).join("|")})\\s*[:.—–-]?\\s*`, "i"),
        "",
      )
      .trim();
    if (trailing) explanationParts.unshift(trailing);
  }

  const findings = splitItems(findingsBlock).slice(0, 3).map(parseFinding);
  const recommendations = splitItems(actionsBlock).slice(0, 3).map(parseRecommendation);
  const missingInformation = splitItems(missingBlock)
    .slice(0, 3)
    .map((item) => stripMarkdown(item.replace(/\r?\n/g, " ")));
  const dataWarnings = splitItems(warningBlock)
    .slice(0, 3)
    .map((item) => stripMarkdown(item.replace(/\r?\n/g, " ")));

  const executiveSummary = summary
    .split(/\r?\n/)
    .map((line) => stripMarkdown(line))
    .filter(Boolean)
    .join(" ");

  const structured = Boolean(
    executiveSummary || findings.length || recommendations.length || detected.status !== "unknown",
  );

  return {
    status: detected.status,
    statusLabel: detected.label,
    statusExplanation: explanationParts.join(" ").trim(),
    executiveSummary,
    findings,
    recommendations,
    missingInformation,
    dataWarnings,
    raw,
    structured,
  };
}
