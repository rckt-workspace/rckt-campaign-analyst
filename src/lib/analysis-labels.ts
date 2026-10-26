/** Human labels for saved analyses. Pure: safe to import from components and tests. */
import type { AnalysisSummary } from "./analysis-history.functions";

/** Friendly, safe labels. Provider details and raw codes are never shown. */
export function describeAnalysis(item: AnalysisSummary): string {
  if (item.status === "running") return "En curso";
  if (item.status === "succeeded")
    return item.usedFallback ? "Completado con modelo alternativo" : "Completado";
  switch (item.errorCode) {
    case "AI_INVALID_RESPONSE":
      return "Fallido: el informe no cumplió el formato";
    case "AI_TIMEOUT":
      return "Fallido: el proveedor tardó demasiado";
    case "ANALYSIS_ABANDONED":
      return "Interrumpido antes de terminar";
    default:
      return "Fallido";
  }
}
