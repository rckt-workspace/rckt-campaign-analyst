import { test } from "node:test";
import assert from "node:assert/strict";
import { describeAnalysis } from "./analysis-labels";
import type { AnalysisSummary } from "./analysis-history.functions";

const base: AnalysisSummary = {
  id: "00000000-0000-4000-8000-000000000001",
  status: "succeeded",
  errorCode: null,
  campaignName: "Prueba",
  createdAt: "2026-10-26T10:00:00Z",
  completedAt: "2026-10-26T10:00:05Z",
  modelUsed: "primary",
  usedFallback: false,
  formatValid: true,
  latencyMs: 5000,
};

test("un informe completado con el modelo principal se describe como completado", () => {
  assert.equal(describeAnalysis(base), "Completado");
});

test("un informe completado con el modelo alternativo lo dice sin cifras ni nombres de modelo", () => {
  assert.equal(describeAnalysis({ ...base, usedFallback: true }), "Completado con modelo alternativo");
});

test("un análisis en curso se describe como tal", () => {
  assert.equal(describeAnalysis({ ...base, status: "running", completedAt: null }), "En curso");
});

test("cada fallo muestra un texto propio y ningún código crudo", () => {
  const cases: Array<[string, string]> = [
    ["AI_INVALID_RESPONSE", "Fallido: el informe no cumplió el formato"],
    ["AI_TIMEOUT", "Fallido: el proveedor tardó demasiado"],
    ["ANALYSIS_ABANDONED", "Interrumpido antes de terminar"],
    ["AI_HTTP_ERROR", "Fallido"],
    ["AI_NETWORK", "Fallido"],
  ];
  for (const [code, label] of cases) {
    const text = describeAnalysis({ ...base, status: "failed", errorCode: code, completedAt: base.completedAt });
    assert.equal(text, label);
    assert.ok(!text.includes("AI_"), `label must not show the raw code ${code}`);
  }
});
