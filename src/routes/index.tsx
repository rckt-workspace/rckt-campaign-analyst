import { createFileRoute } from "@tanstack/react-router";
import { AppHeader } from "@/components/campaign/AppHeader";
import { HeroIntro } from "@/components/campaign/HeroIntro";
import { CampaignInputPanel } from "@/components/campaign/CampaignInputPanel";
import { ExecutiveReportPanel } from "@/components/campaign/ExecutiveReportPanel";

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
  return (
    <div className="min-h-screen bg-background">
      <AppHeader />
      <main className="pb-16">
        <HeroIntro />
        <div className="mx-auto grid max-w-7xl gap-5 px-5 md:px-8 lg:grid-cols-2 lg:gap-6">
          <CampaignInputPanel />
          <ExecutiveReportPanel />
        </div>
      </main>
    </div>
  );
}
