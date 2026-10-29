import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  describeAction,
  describeResourceType,
  formatOccurredAt,
  SOURCE_LABELS,
  type AuditEvent,
} from "@/lib/admin-audit";

export type AuditState = {
  status: "idle" | "loading" | "ready" | "error";
  events: AuditEvent[];
  nextCursor: number | null;
  errorMessage: string | null;
  loadingMore: boolean;
};

/**
 * Events in the order FastAPI sends them. The id is shown only to identify an event, never as a claim about when
 * transactions were committed. Each row is a stacked block, so no horizontal scroll appears on a phone.
 */
export function AuditPanel({ state, onLoadMore }: { state: AuditState; onLoadMore: () => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Auditoría</CardTitle>
        <CardDescription>
          Operaciones registradas de la organización, de la más reciente a la más antigua. Las horas
          están en UTC.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {state.status === "loading" ? (
          <p role="status" className="text-sm text-muted-foreground">
            Cargando eventos…
          </p>
        ) : null}
        {state.status === "error" ? (
          <p role="alert" className="text-sm text-destructive">
            {state.errorMessage}
          </p>
        ) : null}
        {state.status === "ready" && state.events.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Todavía no hay operaciones registradas en esta organización.
          </p>
        ) : null}
        {state.events.length > 0 ? (
          <ol className="grid gap-2" aria-label="Eventos de auditoría">
            {state.events.map((event) => (
              <li
                key={event.id}
                className="grid gap-1 rounded-xl border border-border bg-surface-elevated p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start sm:gap-4"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">
                    {describeAction(event.action)}
                  </p>
                  <p className="mt-0.5 break-all text-xs text-muted-foreground">
                    {describeResourceType(event.resourceType)} · {event.resourceId}
                  </p>
                  <p className="mt-0.5 break-all text-xs text-muted-foreground">
                    Origen: {SOURCE_LABELS[event.source]}
                    {event.actorUserId ? ` · Usuario ${event.actorUserId}` : ""}
                  </p>
                </div>
                <div className="text-left sm:text-right">
                  <time
                    dateTime={event.occurredAt}
                    className="text-xs tabular-nums text-foreground"
                  >
                    {formatOccurredAt(event.occurredAt)}
                  </time>
                  <p className="text-[11px] text-muted-foreground">Evento {event.id}</p>
                </div>
              </li>
            ))}
          </ol>
        ) : null}
        {state.nextCursor !== null ? (
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onLoadMore}
              disabled={state.loadingMore}
            >
              {state.loadingMore ? "Cargando…" : "Cargar eventos anteriores"}
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
