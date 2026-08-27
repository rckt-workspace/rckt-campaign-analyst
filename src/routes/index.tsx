import { useCallback, useRef, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useServerFn } from "@tanstack/react-start";
import { AppHeader } from "@/components/campaign/AppHeader";
import { HeroIntro } from "@/components/campaign/HeroIntro";
import { CampaignInputPanel } from "@/components/campaign/CampaignInputPanel";
import {
  ExecutiveReportPanel,
  type AnalysisStatus,
} from "@/components/campaign/ExecutiveReportPanel";
import { analyzeCampaign } from "@/lib/campaign-analysis.functions";
import type { NormalizedCampaign } from "@/lib/campaign-metrics";
import { analyzeMetrics } from "@/lib/metrics-engine";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "RCKT Campaign Analyst — Análisis ejecutivo de campañas" },
      {
        name: "description",
        content:
          "Convierte métricas de campañas de marketing en un análisis ejecutivo claro: hallazgos, oportunidades y acciones recomendadas.",
      },
      { property: "og:title", content: "RCKT Campaign Analyst" },
      {
        property: "og:description",
        content:
          "Analiza resultados de campañas y convierte datos de marketing en decisiones claras.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

function Index() {
  const runAnalysis = useServerFn(analyzeCampaign);
  const [status, setStatus] = useState<AnalysisStatus>("idle");
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [dataWarnings, setDataWarnings] = useState<string[]>([]);
  const lastPayload = useRef<NormalizedCampaign | null>(null);
  const inFlight = useRef(false);

  const analyze = useCallback(
    async (data: NormalizedCampaign) => {
      if (inFlight.current) return;
      inFlight.current = true;
      lastPayload.current = data;
      setStatus("loading");
      setAnalysis(null);
      setDataWarnings(
        analyzeMetrics(data)
          .warnings.filter((warning) => warning.severity !== "info")
          .map((warning) => warning.message),
      );
      try {
        const result = await runAnalysis({ data });
        setAnalysis(result.analysis);
        setStatus("success");
      } catch (error) {
        console.error("Fallo el análisis de campaña", error);
        setStatus("error");
      } finally {
        inFlight.current = false;
      }
    },
    [runAnalysis],
  );

  const retry = useCallback(() => {
    if (lastPayload.current) void analyze(lastPayload.current);
  }, [analyze]);

  return (
    <div className="min-h-screen bg-background">
      <AppHeader />
      <main className="pb-16">
        <HeroIntro />
        <div className="mx-auto grid max-w-7xl gap-5 px-5 md:px-8 lg:grid-cols-2 lg:gap-6">
          <CampaignInputPanel
            isAnalyzing={status === "loading"}
            onAnalyze={(data) => void analyze(data)}
          />
          <ExecutiveReportPanel
            status={status}
            analysis={analysis}
            dataWarnings={dataWarnings}
            onRetry={retry}
          />
        </div>
      </main>
    </div>
  );
}
