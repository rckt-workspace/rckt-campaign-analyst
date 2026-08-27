/**
 * Capa determinística de métricas (Sprint 6).
 * La aplicación calcula; la IA interpreta. Sin persistencia ni servicios externos.
 */

import type { NormalizedCampaign } from "./campaign-metrics";

export type WarningType =
  | "metric_mismatch"
  | "invalid_value"
  | "impossible_relationship"
  | "missing_critical_metric"
  | "format_uncertainty";

export type WarningSeverity = "info" | "warning" | "critical";

export type DataWarning = {
  type: WarningType;
  metric: string;
  reported: number | null;
  calculated: number | null;
  severity: WarningSeverity;
  message: string;
};

export type Comparison = {
  metric: string;
  current: number;
  previous: number;
  changePercent: number;
};

export type CalculatedMetrics = {
  ctr: number | null;
  cpc: number | null;
  cpl: number | null;
  cpa: number | null;
  roas: number | null;
  conversionRate: number | null;
  frequency: number | null;
};

export type MetricsReport = {
  calculated: CalculatedMetrics;
  comparisons: Comparison[];
  warnings: DataWarning[];
};

/** División segura: nunca devuelve NaN, Infinity ni valores sin sentido. */
function safeDivide(numerator: number | null, denominator: number | null): number | null {
  if (numerator == null || denominator == null) return null;
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator)) return null;
  if (denominator === 0) return null;
  const value = numerator / denominator;
  return Number.isFinite(value) ? value : null;
}

const round = (value: number, decimals = 2) => {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
};

export function calculateMetrics(data: NormalizedCampaign): CalculatedMetrics {
  const ctr = safeDivide(data.clicks, data.impressions);
  const conversionRate = safeDivide(data.conversions, data.clicks);
  return {
    ctr: ctr == null ? null : round(ctr * 100, 4),
    cpc: nullableRound(safeDivide(data.investment, data.clicks)),
    cpl: nullableRound(safeDivide(data.investment, data.leads)),
    cpa: nullableRound(safeDivide(data.investment, data.conversions)),
    roas: nullableRound(safeDivide(data.revenue, data.investment), 4),
    conversionRate: conversionRate == null ? null : round(conversionRate * 100, 4),
    frequency: nullableRound(safeDivide(data.impressions, data.reach), 4),
  };
}

function nullableRound(value: number | null, decimals = 2) {
  return value == null ? null : round(value, decimals);
}

/**
 * Tolerancia de redondeo: se acepta hasta 3% de diferencia relativa
 * (o una diferencia absoluta mínima para valores pequeños).
 */
function isWithinTolerance(reported: number, calculated: number) {
  const diff = Math.abs(reported - calculated);
  const scale = Math.max(Math.abs(reported), Math.abs(calculated));
  if (scale === 0) return true;
  if (diff <= 0.01) return true;
  return diff / scale <= 0.03;
}

const MISMATCH_MESSAGES: Record<string, string> = {
  ROAS: "El ROAS reportado no coincide con la relación entre inversión e ingresos.",
  CTR: "El CTR reportado no coincide con la relación entre clics e impresiones.",
  CPC: "El CPC reportado no coincide con la relación entre inversión y clics.",
  CPL: "El CPL reportado no coincide con la relación entre inversión y leads.",
  Frecuencia: "La frecuencia reportada no coincide con la relación entre impresiones y alcance.",
};

function mismatchWarnings(data: NormalizedCampaign, calc: CalculatedMetrics): DataWarning[] {
  const pairs: Array<[string, number | null, number | null]> = [
    ["ROAS", data.roas, calc.roas],
    ["CTR", data.ctr, calc.ctr],
    ["CPC", data.cpc, calc.cpc],
    ["CPL", data.cpl, calc.cpl],
    ["Frecuencia", data.frequency, calc.frequency],
  ];

  const warnings: DataWarning[] = [];
  for (const [metric, reported, calculated] of pairs) {
    if (reported == null || calculated == null) continue;
    if (isWithinTolerance(reported, calculated)) continue;
    warnings.push({
      type: "metric_mismatch",
      metric,
      reported,
      calculated,
      severity: "warning",
      message: MISMATCH_MESSAGES[metric] ?? `El ${metric} reportado no coincide con el calculado.`,
    });
  }
  return warnings;
}

const NEGATIVE_CHECKS: Array<[keyof NormalizedCampaign, string]> = [
  ["investment", "Inversión"],
  ["impressions", "Impresiones"],
  ["reach", "Alcance"],
  ["frequency", "Frecuencia"],
  ["clicks", "Clics"],
  ["ctr", "CTR"],
  ["cpc", "CPC"],
  ["leads", "Leads"],
  ["cpl", "CPL"],
  ["conversions", "Ventas"],
  ["revenue", "Ingresos"],
  ["roas", "ROAS"],
];

function validityWarnings(data: NormalizedCampaign): DataWarning[] {
  const warnings: DataWarning[] = [];

  for (const [key, label] of NEGATIVE_CHECKS) {
    const value = data[key];
    if (typeof value === "number" && value < 0) {
      warnings.push({
        type: "invalid_value",
        metric: label,
        reported: value,
        calculated: null,
        severity: "critical",
        message: `El valor de ${label} es negativo, lo que no es posible en una campaña.`,
      });
    }
  }

  if (data.clicks != null && data.impressions != null && data.clicks > data.impressions) {
    warnings.push({
      type: "impossible_relationship",
      metric: "Clics / Impresiones",
      reported: data.clicks,
      calculated: data.impressions,
      severity: "critical",
      message: "Los clics reportados superan las impresiones, algo que no puede ocurrir.",
    });
  }

  if (data.reach != null && data.impressions != null && data.reach > data.impressions) {
    warnings.push({
      type: "impossible_relationship",
      metric: "Alcance / Impresiones",
      reported: data.reach,
      calculated: data.impressions,
      severity: "warning",
      message: "El alcance reportado supera las impresiones, algo que no debería ocurrir.",
    });
  }

  if (data.conversions != null && data.clicks != null && data.conversions > data.clicks) {
    warnings.push({
      type: "impossible_relationship",
      metric: "Ventas / Clics",
      reported: data.conversions,
      calculated: data.clicks,
      severity: "warning",
      message: "Las ventas reportadas superan los clics registrados en la campaña.",
    });
  }

  const salesObjective = /venta|compra|ecommerce|conversion/i.test(data.objective ?? "");
  if (salesObjective && data.revenue == null && data.conversions == null) {
    warnings.push({
      type: "missing_critical_metric",
      metric: "Ventas / Ingresos",
      reported: null,
      calculated: null,
      severity: "info",
      message:
        "El objetivo es ventas, pero no se entregaron ventas ni ingresos: no se puede evaluar rentabilidad.",
    });
  }

  return warnings;
}

const COMPARABLE: Array<[keyof NormalizedCampaign, string]> = [
  ["investment", "Inversión"],
  ["impressions", "Impresiones"],
  ["reach", "Alcance"],
  ["clicks", "Clics"],
  ["ctr", "CTR"],
  ["cpc", "CPC"],
  ["leads", "Leads"],
  ["cpl", "CPL"],
  ["conversions", "Ventas"],
  ["revenue", "Ingresos"],
  ["roas", "ROAS"],
  ["frequency", "Frecuencia"],
];

export function buildComparisons(data: NormalizedCampaign): Comparison[] {
  const previous = data.previous ?? {};
  const comparisons: Comparison[] = [];

  for (const [key, label] of COMPARABLE) {
    const current = data[key];
    const before = (previous as Record<string, number | null | undefined>)[key as string];
    if (typeof current !== "number" || typeof before !== "number") continue;
    const change = safeDivide(current - before, before);
    if (change == null) continue;
    comparisons.push({
      metric: label,
      current,
      previous: before,
      changePercent: round(change * 100),
    });
  }
  return comparisons;
}

export function analyzeMetrics(data: NormalizedCampaign): MetricsReport {
  const calculated = calculateMetrics(data);
  return {
    calculated,
    comparisons: buildComparisons(data),
    warnings: [...validityWarnings(data), ...mismatchWarnings(data, calculated)],
  };
}

const CALC_LABELS: Array<[keyof CalculatedMetrics, string]> = [
  ["ctr", "CTR calculado (%)"],
  ["cpc", "CPC calculado"],
  ["cpl", "CPL calculado"],
  ["cpa", "Costo por venta calculado"],
  ["roas", "ROAS calculado"],
  ["conversionRate", "Tasa de conversión calculada (%)"],
  ["frequency", "Frecuencia calculada"],
];

const NOT_CALCULABLE = "No calculable con los datos disponibles";

const formatNumber = (value: number) =>
  new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 }).format(value);

/** Bloque de texto con los datos ya verificados por la aplicación. */
export function buildDeterministicBlock(data: NormalizedCampaign, report: MetricsReport): string {
  const parts: string[] = [];

  const calcLines = CALC_LABELS.map(([key, label]) => {
    const value = report.calculated[key];
    return `${label}: ${value == null ? NOT_CALCULABLE : formatNumber(value)}`;
  });
  parts.push(
    `MÉTRICAS CALCULADAS POR LA APLICACIÓN (ya verificadas, prioriza estos valores):\n${calcLines.join("\n")}`,
  );

  if (report.comparisons.length) {
    const lines = report.comparisons.map(
      (item) =>
        `${item.metric}: ${formatNumber(item.previous)} → ${formatNumber(item.current)} (${
          item.changePercent > 0 ? "+" : ""
        }${formatNumber(item.changePercent)}%)`,
    );
    parts.push(`VARIACIONES CALCULADAS FRENTE AL PERIODO ANTERIOR:\n${lines.join("\n")}`);
  }

  if (report.warnings.length) {
    const lines = report.warnings.map((warning) => {
      const detail =
        warning.reported != null && warning.calculated != null
          ? ` (reportado: ${formatNumber(warning.reported)} · calculado: ${formatNumber(warning.calculated)})`
          : "";
      return `[${warning.severity}] ${warning.metric}: ${warning.message}${detail}`;
    });
    parts.push(`ADVERTENCIAS DETECTADAS POR LA APLICACIÓN:\n${lines.join("\n")}`);
  }

  return parts.join("\n\n");
}
