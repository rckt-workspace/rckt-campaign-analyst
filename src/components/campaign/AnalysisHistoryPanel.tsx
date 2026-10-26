import { FileText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { AnalysisSummary } from "@/lib/analysis-history.functions";
import { describeAnalysis } from "@/lib/analysis-labels";

export type AnalysisHistoryStatus = "loading" | "ready" | "error";

const dateFormatter = new Intl.DateTimeFormat("es-CO", { dateStyle: "medium", timeStyle: "short" });

export function AnalysisHistoryPanel({
  status,
  items,
  hasMore,
  loadingMore,
  selectedId,
  onOpen,
  onLoadMore,
  onRetry,
}: {
  status: AnalysisHistoryStatus;
  items: AnalysisSummary[];
  hasMore: boolean;
  loadingMore: boolean;
  selectedId: string | null;
  onOpen: (id: string) => void;
  onLoadMore: () => void;
  onRetry: () => void;
}) {
  return (
    <section className="panel flex min-w-0 flex-col p-5 md:p-6">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-border bg-surface-elevated">
          <FileText className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold tracking-tight text-foreground">
            Historial de informes
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Informes de tu organización, del más reciente al más antiguo. Abre uno para leerlo de
            nuevo.
          </p>
        </div>
      </div>

      <div aria-live="polite" className="mt-5">
        {status === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Cargando historial…
          </p>
        ) : null}

        {status === "error" ? (
          <div className="flex flex-col items-start gap-3">
            <p className="text-sm text-destructive">No pudimos cargar el historial de informes.</p>
            <Button type="button" variant="outline" onClick={onRetry} className="rounded-xl">
              Reintentar
            </Button>
          </div>
        ) : null}

        {status === "ready" && items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Todavía no hay informes guardados. El primero aparecerá aquí al analizar una campaña.
          </p>
        ) : null}

        {status === "ready" && items.length > 0 ? (
          <>
            <ul className="divide-y divide-border">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(item.id)}
                    aria-current={selectedId === item.id ? "true" : undefined}
                    className={`flex w-full flex-col gap-1 py-3 text-left transition-colors duration-200 sm:flex-row sm:items-center sm:justify-between ${
                      selectedId === item.id ? "text-primary" : "text-foreground hover:text-primary"
                    }`}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {item.campaignName ?? "Campaña sin nombre"}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {describeAnalysis(item)}
                      </span>
                    </span>
                    <time
                      dateTime={item.createdAt}
                      className="shrink-0 text-xs text-muted-foreground"
                    >
                      {dateFormatter.format(new Date(item.createdAt))}
                    </time>
                  </button>
                </li>
              ))}
            </ul>
            {hasMore ? (
              <Button
                type="button"
                variant="outline"
                onClick={onLoadMore}
                disabled={loadingMore}
                className="mt-4 justify-center rounded-xl"
              >
                {loadingMore ? "Cargando…" : "Cargar más"}
              </Button>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}
