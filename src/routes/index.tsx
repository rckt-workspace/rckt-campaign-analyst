import { useCallback, useEffect, useRef, useState } from "react";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { AppHeader } from "@/components/campaign/AppHeader";
import { HeroIntro } from "@/components/campaign/HeroIntro";
import { CampaignInputPanel, type SaveResult } from "@/components/campaign/CampaignInputPanel";
import {
  ExecutiveReportPanel,
  type AnalysisStatus,
} from "@/components/campaign/ExecutiveReportPanel";
import {
  SavedCampaignsPanel,
  type SavedCampaignsStatus,
} from "@/components/campaign/SavedCampaignsPanel";
import { getAccount, signOut } from "@/lib/auth.functions";
import { ANALYSIS_ERRORS } from "@/lib/analysis-errors";
import {
  analyzeCampaign,
  listSavedCampaigns,
  saveCampaign,
  type SavedCampaign,
} from "@/lib/campaign-api.functions";
import type { NormalizedCampaign } from "@/lib/campaign-metrics";
import { analyzeMetrics } from "@/lib/metrics-engine";

const NOTICES: Partial<Record<string, string>> = {
  [ANALYSIS_ERRORS.FORBIDDEN]:
    "Tu rol permite consultar los informes de tu organización, pero no generar análisis nuevos.",
  [ANALYSIS_ERRORS.NO_ORGANIZATION]:
    "Tu cuenta todavía no tiene acceso a ninguna organización. Solicita la asignación a tu contacto en RCKT.",
};

const SAVE_NOTICES: Record<string, SaveResult> = {
  [ANALYSIS_ERRORS.FORBIDDEN]: {
    tone: "warn",
    text: "Tu rol permite consultar campañas guardadas, pero no registrar nuevas.",
  },
  [ANALYSIS_ERRORS.NO_ORGANIZATION]: {
    tone: "warn",
    text: "Tu cuenta todavía no tiene acceso a ninguna organización. Solicita la asignación a tu contacto en RCKT.",
  },
  [ANALYSIS_ERRORS.CAMPAIGN_INVALID]: {
    tone: "warn",
    text: "Algún valor no puede guardarse tal como está escrito. Los conteos deben ser enteros, los importes admiten hasta 2 decimales y los costos o ratios hasta 4, sin cifras negativas.",
  },
};
const SAVE_FALLBACK: SaveResult = {
  tone: "warn",
  text: "No pudimos guardar la campaña. Inténtalo de nuevo.",
};

export const Route = createFileRoute("/")({
  beforeLoad: async () => {
    // Protected route: without a valid session the user never sees the application. Server functions
    // also verify the session on every call, so this check is not the only barrier.
    const account = await getAccount();
    if (!account) throw redirect({ to: "/login", search: { expired: false } });
    return { account };
  },
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
  const { account } = Route.useRouteContext();
  const navigate = useNavigate();
  const [status, setStatus] = useState<AnalysisStatus>("idle");
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [dataWarnings, setDataWarnings] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveResult | null>(null);
  const [campaigns, setCampaigns] = useState<SavedCampaign[]>([]);
  const [campaignsStatus, setCampaignsStatus] = useState<SavedCampaignsStatus>("loading");
  const lastPayload = useRef<NormalizedCampaign | null>(null);
  const inFlight = useRef(false);

  const refreshCampaigns = useCallback(async () => {
    try {
      const result = await listSavedCampaigns({ data: {} });
      setCampaigns(result.campaigns);
      setCampaignsStatus("ready");
    } catch (error) {
      const code = error instanceof Error ? error.message : "";
      if (code === ANALYSIS_ERRORS.SESSION_EXPIRED) {
        await navigate({ to: "/login", search: { expired: true } });
        return;
      }
      console.error("No se pudieron cargar las campañas guardadas", code);
      setCampaignsStatus("error");
    }
  }, [navigate]);

  useEffect(() => {
    void refreshCampaigns();
  }, [refreshCampaigns]);

  const save = useCallback(
    async (data: NormalizedCampaign, source: "text" | "form") => {
      setSaving(true);
      setSaveResult(null);
      try {
        await saveCampaign({ data: { data, source } });
        setSaveResult({ tone: "ok", text: "Campaña guardada en tu organización." });
        await refreshCampaigns();
      } catch (error) {
        const code = error instanceof Error ? error.message : "";
        if (code === ANALYSIS_ERRORS.SESSION_EXPIRED) {
          await navigate({ to: "/login", search: { expired: true } });
          return;
        }
        console.error("Fallo al guardar la campaña", code);
        setSaveResult(SAVE_NOTICES[code] ?? SAVE_FALLBACK);
      } finally {
        setSaving(false);
      }
    },
    [navigate, refreshCampaigns],
  );

  const analyze = useCallback(
    async (data: NormalizedCampaign) => {
      if (inFlight.current) return;
      inFlight.current = true;
      lastPayload.current = data;
      setStatus("loading");
      setAnalysis(null);
      setNotice(null);
      setDataWarnings(
        analyzeMetrics(data)
          .warnings.filter((warning) => warning.severity !== "info")
          .map((warning) => warning.message),
      );
      try {
        const result = await analyzeCampaign({ data: { data } });
        setAnalysis(result.analysis);
        setStatus("success");
      } catch (error) {
        const code = error instanceof Error ? error.message : "";
        if (code === ANALYSIS_ERRORS.SESSION_EXPIRED) {
          // The session is gone: nothing is retried and no provider call is made.
          await navigate({ to: "/login", search: { expired: true } });
          return;
        }
        console.error("Fallo el análisis de campaña", code);
        setNotice(NOTICES[code] ?? null);
        setStatus("error");
      } finally {
        inFlight.current = false;
      }
    },
    [navigate],
  );

  const retry = useCallback(() => {
    if (lastPayload.current) void analyze(lastPayload.current);
  }, [analyze]);

  const handleSignOut = useCallback(async () => {
    await signOut();
    await navigate({ to: "/login", search: { expired: false } });
  }, [navigate]);

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={account.email} onSignOut={() => void handleSignOut()} />
      <main className="pb-16">
        <HeroIntro />
        {notice ? (
          <div
            role="alert"
            className="mx-auto mb-5 max-w-7xl rounded-xl border border-border bg-surface-elevated px-5 py-3 text-sm text-foreground md:px-8"
          >
            {notice}
          </div>
        ) : null}
        <div className="mx-auto grid max-w-7xl gap-5 px-5 md:px-8 lg:grid-cols-2 lg:gap-6">
          <CampaignInputPanel
            isAnalyzing={status === "loading"}
            onAnalyze={(data) => void analyze(data)}
            isSaving={saving}
            saveResult={saveResult}
            onSave={(data, source) => void save(data, source)}
          />
          <ExecutiveReportPanel
            status={status}
            analysis={analysis}
            dataWarnings={dataWarnings}
            onRetry={retry}
          />
        </div>
        <div className="mx-auto mt-5 max-w-7xl px-5 md:px-8 lg:mt-6">
          <SavedCampaignsPanel status={campaignsStatus} campaigns={campaigns} />
        </div>
      </main>
    </div>
  );
}
