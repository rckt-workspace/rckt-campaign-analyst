import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  CircleDashed,
  FileSearch,
  Loader2,
  Radar,
  RotateCcw,
  Target,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  parseCampaignReport,
  type CampaignReport,
  type MetricDirection,
  type Priority,
  type ReportStatus,
} from "@/lib/report-parser";

const SECTIONS = [
  "Estado general",
  "Resumen ejecutivo",
  "Principales hallazgos",
  "Qué haría ahora",
  "Información que ayudaría a profundizar",
];

const LOADING_STEPS = [
  "Analizando métricas",
  "Identificando hallazgos",
  "Preparando recomendaciones",
];

export type AnalysisStatus = "idle" | "loading" | "success" | "error";

export function ExecutiveReportPanel({
  status = "idle",
  analysis = null,
  dataWarnings = [],
  onRetry,
}: {
  status?: AnalysisStatus;
  analysis?: string | null;
  /** Advertencias determinísticas calculadas por la aplicación. */
  dataWarnings?: string[];
  onRetry?: () => void;
}) {
  const report = useMemo(
    () => (status === "success" && analysis ? parseCampaignReport(analysis) : null),
    [status, analysis],
  );

  return (
    <section className="panel flex min-w-0 flex-col p-5 md:p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-border bg-surface-elevated">
            <BarChart3 className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight text-foreground">
              Análisis ejecutivo
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Resultado estructurado de la campaña analizada.
            </p>
          </div>
        </div>
        {report ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onRetry}
            className="w-full shrink-0 gap-2 rounded-xl border-border sm:w-auto bg-surface-elevated transition-colors duration-200 hover:border-primary/50"
          >
            <RotateCcw className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            Analizar nuevamente
          </Button>
        ) : null}
      </div>

      <div aria-live="polite" className="mt-5 flex flex-1 flex-col">
        {status === "loading" ? (
          <LoadingState />
        ) : status === "error" ? (
          <ErrorState onRetry={onRetry} />
        ) : report ? (
          <ReportView report={report} dataWarnings={dataWarnings} />
        ) : (
          <EmptyState />
        )}
      </div>
    </section>
  );
}

function LoadingState() {
  const [step, setStep] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => {
      setStep((prev) => (prev + 1) % LOADING_STEPS.length);
    }, 2200);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="flex flex-1 animate-in flex-col items-center justify-center rounded-xl border border-dashed border-primary/40 bg-background/40 px-6 py-14 text-center duration-500 fade-in">
      <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden="true" />
      <p className="mt-5 text-sm font-medium text-foreground">
        Analizando rendimiento y oportunidades…
      </p>
      <ul className="mt-5 flex flex-col items-center gap-2">
        {LOADING_STEPS.map((label, index) => (
          <li
            key={label}
            className={cn(
              "flex items-center gap-2 text-sm transition-opacity duration-500",
              index === step ? "text-foreground opacity-100" : "text-muted-foreground opacity-50",
            )}
          >
            <CircleDashed
              className={cn("h-3.5 w-3.5", index === step && "animate-spin text-primary")}
              aria-hidden="true"
            />
            {label}
          </li>
        ))}
      </ul>
      <div className="mt-6 w-full max-w-sm space-y-2" aria-hidden="true">
        <div className="h-3 w-2/3 animate-pulse rounded-full bg-surface-elevated" />
        <div className="h-3 w-full animate-pulse rounded-full bg-surface-elevated" />
        <div className="h-3 w-5/6 animate-pulse rounded-full bg-surface-elevated" />
      </div>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry?: (() => void) | undefined }) {
  return (
    <div className="flex flex-1 animate-in flex-col items-center justify-center rounded-xl border border-destructive/40 bg-destructive/10 px-6 py-14 text-center duration-500 fade-in">
      <AlertTriangle className="h-6 w-6 text-destructive" aria-hidden="true" />
      <p className="mt-5 text-sm font-medium text-foreground">
        No pudimos completar el análisis en este momento. Intenta nuevamente.
      </p>
      <Button
        type="button"
        variant="outline"
        onClick={onRetry}
        className="mt-5 gap-2 rounded-xl border-border bg-surface-elevated"
      >
        <RotateCcw className="h-4 w-4" aria-hidden="true" />
        Reintentar
      </Button>
    </div>
  );
}

function EmptyState() {
  return (
    <>
      <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-background/40 px-6 py-14 text-center">
        <div className="grid h-14 w-14 place-items-center rounded-2xl border border-border bg-surface-elevated">
          <Radar className="h-6 w-6 text-primary" aria-hidden="true" />
        </div>
        <p className="mt-5 text-sm font-medium text-foreground">Tu análisis aparecerá aquí</p>
        <p className="mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground">
          Cuando analices una campaña, encontrarás aquí los principales hallazgos, oportunidades y
          acciones recomendadas.
        </p>
      </div>

      <div className="mt-5">
        <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
          Estructura del informe
        </p>
        <ul className="mt-3 flex flex-col gap-2">
          {SECTIONS.map((section, index) => (
            <li
              key={section}
              className="flex items-center gap-3 rounded-lg border border-border/70 bg-surface-elevated/60 px-3.5 py-2.5 transition-colors duration-200 hover:border-primary/40"
            >
              <span className="w-4 shrink-0 text-[11px] font-semibold text-primary">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 truncate text-sm text-muted-foreground">{section}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function ReportView({
  report,
  dataWarnings = [],
}: {
  report: CampaignReport;
  dataWarnings?: string[];
}) {
  const warnings = [...dataWarnings, ...report.dataWarnings].filter(
    (item, index, list) => item && list.indexOf(item) === index,
  );

  if (!report.structured) {
    return (
      <article className="min-w-0 animate-in rounded-xl border border-border bg-background/40 px-4 py-5 text-sm leading-relaxed whitespace-pre-line text-muted-foreground duration-500 fade-in md:px-5">
        {report.raw}
      </article>
    );
  }

  const insufficient = report.status === "insufficient";

  return (
    <div className="flex min-w-0 animate-in flex-col gap-4 duration-500 fade-in slide-in-from-bottom-2">
      <StatusCard report={report} />

      {insufficient ? (
        <p className="text-sm leading-relaxed text-muted-foreground">
          Todavía no hay suficiente información para evaluar completamente esta campaña. A
          continuación, lo que sí podemos observar y qué datos permitirían continuar.
        </p>
      ) : null}

      {warnings.length ? (
        <section className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
            <h3 className="text-[11px] font-semibold tracking-[0.14em] text-warning uppercase">
              Dato a revisar
            </h3>
          </div>
          <ul className="mt-2.5 flex flex-col gap-2">
            {warnings.map((warning, index) => (
              <li key={index} className="min-w-0 text-sm leading-relaxed text-foreground/90">
                {warning}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {report.executiveSummary ? (
        <section className="rounded-xl border border-primary/30 bg-primary/8 px-4 py-4 md:px-5 md:py-5">
          <h3 className="text-[11px] font-semibold tracking-[0.14em] text-primary uppercase">
            Resumen ejecutivo
          </h3>
          <p className="mt-2.5 text-[15px] leading-relaxed font-medium text-foreground md:text-base">
            {report.executiveSummary}
          </p>
        </section>
      ) : null}

      {report.findings.length ? (
        <section>
          <SectionTitle
            icon={<FileSearch className="h-3.5 w-3.5" />}
            label="Principales hallazgos"
          />
          <ul className="mt-3 flex flex-col gap-2.5">
            {report.findings.map((finding, index) => (
              <li
                key={index}
                className="min-w-0 rounded-xl border border-border bg-surface-elevated/60 px-4 py-3.5 transition-colors duration-200 hover:border-primary/40"
              >
                <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-3">
                  <span className="text-[11px] font-semibold tabular-nums text-primary">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div className="min-w-0">
                    <h4 className="text-sm font-semibold text-foreground">{finding.title}</h4>
                    {finding.description ? (
                      <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                        {finding.description}
                      </p>
                    ) : null}
                    {finding.keyMetric ? (
                      <p className="mt-3 flex max-w-full flex-wrap items-baseline gap-x-2 gap-y-1 rounded-lg border border-border bg-background/60 px-2.5 py-1.5">
                        <DirectionIcon direction={finding.direction} />
                        <span className="shrink-0 whitespace-nowrap text-[11px] font-medium tracking-[0.1em] text-muted-foreground uppercase">
                          Dato clave
                        </span>
                        <span className="min-w-0 text-sm font-medium text-foreground">
                          {finding.keyMetric}
                        </span>
                      </p>
                    ) : null}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {report.recommendations.length ? (
        <section>
          <SectionTitle icon={<Target className="h-3.5 w-3.5" />} label="Qué haría ahora" />
          <ul className="mt-3 flex flex-col gap-2.5">
            {report.recommendations.map((item, index) => (
              <li
                key={index}
                className="min-w-0 rounded-xl border border-border bg-background/50 px-4 py-3.5 transition-colors duration-200 hover:border-primary/40"
              >
                <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 sm:flex sm:justify-between">
                  <h4 className="min-w-0 text-sm font-semibold text-foreground">
                    <span className="mr-2 text-primary tabular-nums">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    {item.title}
                  </h4>
                  {item.priority ? <PriorityBadge priority={item.priority} /> : null}
                </div>
                {item.description ? (
                  <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                    {item.description}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {report.missingInformation.length ? (
        <section className="rounded-xl border border-border/70 bg-background/30 px-4 py-3.5">
          <h3 className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            Información que ayudaría a profundizar
          </h3>
          <ul className="mt-2.5 flex flex-col gap-1.5">
            {report.missingInformation.map((item, index) => (
              <li key={index} className="flex min-w-0 gap-2 text-sm text-muted-foreground">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
                <span className="min-w-0">{item}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function SectionTitle({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-2 text-muted-foreground">
      <span className="shrink-0 text-primary" aria-hidden="true">
        {icon}
      </span>
      <h3 className="text-[11px] font-semibold tracking-[0.14em] uppercase">{label}</h3>
    </div>
  );
}

const STATUS_STYLES: Record<ReportStatus, { dot: string; border: string; bg: string }> = {
  good: { dot: "bg-success", border: "border-success/40", bg: "bg-success/10" },
  attention: { dot: "bg-warning", border: "border-warning/40", bg: "bg-warning/10" },
  critical: { dot: "bg-destructive", border: "border-destructive/40", bg: "bg-destructive/10" },
  insufficient: {
    dot: "bg-muted-foreground",
    border: "border-border",
    bg: "bg-surface-elevated/70",
  },
  unknown: { dot: "bg-primary", border: "border-border", bg: "bg-surface-elevated/70" },
};

function StatusCard({ report }: { report: CampaignReport }) {
  const style = STATUS_STYLES[report.status];
  return (
    <section className={cn("rounded-xl border px-4 py-3.5", style.border, style.bg)}>
      <div className="flex min-w-0 items-center gap-2.5">
        <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", style.dot)} aria-hidden="true" />
        <span className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
          Estado general
        </span>
        <span className="min-w-0 text-sm font-semibold text-foreground">{report.statusLabel}</span>
      </div>
      {report.statusExplanation ? (
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {report.statusExplanation}
        </p>
      ) : null}
    </section>
  );
}

const PRIORITY_LABELS: Record<Exclude<Priority, null>, string> = {
  high: "Prioridad alta",
  medium: "Prioridad media",
  low: "Prioridad baja",
};

function PriorityBadge({ priority }: { priority: Exclude<Priority, null> }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-medium",
        priority === "high"
          ? "border-warning/40 bg-warning/10 text-warning"
          : priority === "medium"
            ? "border-primary/40 bg-primary/10 text-primary"
            : "border-border bg-surface-elevated text-muted-foreground",
      )}
    >
      {PRIORITY_LABELS[priority]}
    </span>
  );
}

function DirectionIcon({ direction }: { direction: MetricDirection }) {
  if (direction === "up") {
    return (
      <ArrowUpRight
        className="h-3.5 w-3.5 shrink-0 text-foreground"
        aria-label="variación al alza"
      />
    );
  }
  if (direction === "down") {
    return (
      <ArrowDownRight
        className="h-3.5 w-3.5 shrink-0 text-foreground"
        aria-label="variación a la baja"
      />
    );
  }
  if (direction === "flat") {
    return (
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-foreground" aria-label="sin variación" />
    );
  }
  return null;
}
