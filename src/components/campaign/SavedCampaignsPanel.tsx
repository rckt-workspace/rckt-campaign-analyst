import { FolderOpen, Loader2 } from "lucide-react";
import type { SavedCampaign } from "@/lib/campaign-api.functions";

export type SavedCampaignsStatus = "loading" | "ready" | "error";

const dateFormatter = new Intl.DateTimeFormat("es-CO", { dateStyle: "medium" });

export function SavedCampaignsPanel({
  status,
  campaigns,
}: {
  status: SavedCampaignsStatus;
  campaigns: SavedCampaign[];
}) {
  return (
    <section className="panel flex min-w-0 flex-col p-5 md:p-6">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-border bg-surface-elevated">
          <FolderOpen className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold tracking-tight text-foreground">
            Campañas guardadas
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Campañas registradas en tu organización, de la más reciente a la más antigua.
          </p>
        </div>
      </div>

      <div aria-live="polite" className="mt-5">
        {status === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Cargando campañas…
          </p>
        ) : null}
        {status === "error" ? (
          <p className="text-sm text-destructive">No pudimos cargar las campañas guardadas.</p>
        ) : null}
        {status === "ready" && campaigns.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Todavía no hay campañas guardadas. Usa «Guardar campaña» cuando los datos estén listos.
          </p>
        ) : null}
        {status === "ready" && campaigns.length > 0 ? (
          <ul className="divide-y divide-border">
            {campaigns.map((campaign) => (
              <li
                key={campaign.id}
                className="flex flex-col gap-1 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">{campaign.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {[campaign.channel, campaign.objective, campaign.periodLabel]
                      .filter(Boolean)
                      .join(" · ") || "Sin canal ni objetivo"}
                  </p>
                </div>
                <time
                  dateTime={campaign.createdAt}
                  className="shrink-0 text-xs text-muted-foreground"
                >
                  {dateFormatter.format(new Date(campaign.createdAt))}
                </time>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
