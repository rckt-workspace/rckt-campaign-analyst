import { AlertTriangle, BarChart3, Loader2, Radar, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";

const SECTIONS = [
  "Estado general",
  "Resumen ejecutivo",
  "Principales hallazgos",
  "Recomendaciones",
  "Información adicional",
];

export type AnalysisStatus = "idle" | "loading" | "success" | "error";

export function ExecutiveReportPanel({
  status = "idle",
  analysis = null,
  onRetry,
}: {
  status?: AnalysisStatus;
  analysis?: string | null;
  onRetry?: () => void;
}) {
  return (
    <section className="panel flex min-w-0 flex-col p-5 md:p-6">
      <div className="flex items-start gap-3">
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

      <div aria-live="polite" className="mt-5 flex flex-1 flex-col">
        {status === "loading" ? (
          <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-primary/40 bg-background/40 px-6 py-14 text-center">
            <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden="true" />
            <p className="mt-5 text-sm font-medium text-foreground">
              Analizando rendimiento y oportunidades…
            </p>
            <p className="mt-2 max-w-sm text-sm text-muted-foreground">
              Esto puede tardar unos segundos.
            </p>
          </div>
        ) : status === "error" ? (
          <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-destructive/40 bg-destructive/10 px-6 py-14 text-center">
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
        ) : status === "success" && analysis ? (
          <article className="report min-w-0 rounded-xl border border-border bg-background/40 px-4 py-5 text-sm leading-relaxed text-muted-foreground md:px-5">
            {renderAnalysis(analysis)}
          </article>
        ) : (
          <>
            <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-background/40 px-6 py-14 text-center">
              <div className="grid h-14 w-14 place-items-center rounded-2xl border border-border bg-surface-elevated">
                <Radar className="h-6 w-6 text-primary" aria-hidden="true" />
              </div>
              <p className="mt-5 text-sm font-medium text-foreground">
                Tu análisis aparecerá aquí
              </p>
              <p className="mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground">
                Cuando analices una campaña, encontrarás aquí los principales hallazgos,
                oportunidades y acciones recomendadas.
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
                    <span className="min-w-0 truncate text-sm text-muted-foreground">
                      {section}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

/** Render mínimo de markdown (encabezados, viñetas, negritas) sin dependencias nuevas. */
function renderAnalysis(text: string) {
  const blocks = text.split(/\r?\n/);
  return (
    <div className="flex flex-col gap-2">
      {blocks.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={index} className="h-1" />;

        const heading = trimmed.match(/^#{1,4}\s+(.*)$/);
        if (heading) {
          return (
            <h3
              key={index}
              className="mt-3 text-[11px] font-semibold tracking-[0.14em] text-primary uppercase first:mt-0"
            >
              {inline(heading[1] ?? "")}
            </h3>
          );
        }

        const bullet = trimmed.match(/^([-*•]|\d+[.)])\s+(.*)$/);
        if (bullet) {
          return (
            <p key={index} className="flex min-w-0 gap-2 pl-1">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/70" />
              <span className="min-w-0">{inline(bullet[2] ?? "")}</span>
            </p>
          );
        }

        return (
          <p key={index} className="min-w-0">
            {inline(trimmed)}
          </p>
        );
      })}
    </div>
  );
}

function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={index} className="font-semibold text-foreground">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={index}>{part}</span>
    ),
  );
}
