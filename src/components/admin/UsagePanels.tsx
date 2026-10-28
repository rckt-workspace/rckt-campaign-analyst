import { CheckCircle2, CircleDashed, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  COMPLETENESS_LABEL,
  costState,
  tokenState,
  type UsageReport,
  type ValueState,
} from "@/lib/admin-usage";

const NUMBER = new Intl.NumberFormat("es");

/** A count that is always known. */
function count(value: number): string {
  return NUMBER.format(value);
}

/** A value that may be unknown: shown as "Desconocido", never as zero. */
function maybeCount(value: number | null): string {
  return value === null ? "Desconocido" : NUMBER.format(value);
}

function StateBadge({ state }: { state: ValueState }) {
  if (state === "known") {
    return (
      <Badge variant="secondary" className="gap-1">
        <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
        Conocido
      </Badge>
    );
  }
  if (state === "partial") {
    return (
      <Badge variant="outline" className="gap-1">
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        Parcial
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-1 text-muted-foreground">
      <CircleDashed className="h-3 w-3" aria-hidden="true" />
      Desconocido
    </Badge>
  );
}

function Metric({
  label,
  value,
  state,
  hint,
}: {
  label: string;
  value: string;
  state?: ValueState;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface-elevated p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        {state ? <StateBadge state={state} /> : null}
      </div>
      <p className="mt-2 break-words text-2xl font-semibold tabular-nums text-foreground">
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** Analyses (requests) and provider calls (attempts) are shown in separate sections: they are not the same count. */
export function UsageSummary({ report }: { report: UsageReport }) {
  const promptState = tokenState(
    report.calls.promptTokens,
    report.calls.callsWithPromptReported,
    report.calls.total,
  );
  const completionState = tokenState(
    report.calls.completionTokens,
    report.calls.callsWithCompletionReported,
    report.calls.total,
  );
  return (
    <div className="grid gap-6">
      <section aria-labelledby="analyses-heading" className="grid gap-3">
        <h2 id="analyses-heading" className="text-sm font-semibold text-foreground">
          Análisis solicitados
        </h2>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Metric
            label="Solicitados"
            value={count(report.analyses.requested)}
            hint="Creados en el mes"
          />
          <Metric label="Exitosos" value={count(report.analyses.succeeded)} />
          <Metric label="Fallidos" value={count(report.analyses.failed)} />
          <Metric label="En ejecución" value={count(report.analyses.running)} />
        </div>
        {report.analyses.withoutQuotaRecord > 0 ? (
          <p className="text-xs text-muted-foreground">
            {count(report.analyses.withoutQuotaRecord)} análisis anteriores a los registros de
            cuota: cuentan en solicitados, no en unidades.
          </p>
        ) : null}
      </section>

      <section aria-labelledby="calls-heading" className="grid gap-3">
        <h2 id="calls-heading" className="text-sm font-semibold text-foreground">
          Llamadas al proveedor de IA
        </h2>
        <p className="text-xs text-muted-foreground">
          Cada intento HTTP cuenta por separado. Un reintento o un fallback es otra llamada.
        </p>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Metric label="Llamadas" value={count(report.calls.total)} />
          <Metric label="Correctas" value={count(report.calls.succeeded)} />
          <Metric label="Fallidas" value={count(report.calls.failed)} />
          <Metric
            label="Resultado desconocido"
            value={count(report.calls.reserved + report.calls.unknown)}
            hint={`${count(report.calls.reserved)} reservadas · ${count(report.calls.unknown)} sin confirmar`}
          />
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Metric
            label="Tokens de entrada"
            value={maybeCount(report.calls.promptTokens)}
            state={promptState}
            hint={`${count(report.calls.callsWithPromptReported)} de ${count(report.calls.total)} llamadas lo reportaron`}
          />
          <Metric
            label="Tokens de salida"
            value={maybeCount(report.calls.completionTokens)}
            state={completionState}
            hint={`${count(report.calls.callsWithCompletionReported)} de ${count(report.calls.total)} llamadas lo reportaron`}
          />
        </div>
      </section>
    </div>
  );
}

/** Reported credits, exactly as OpenRouter's usage object describes them. Not an invoice and never converted. */
export function CostPanel({ report }: { report: UsageReport }) {
  const state = costState(report.cost);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Consumo económico reportado</CardTitle>
        <CardDescription>
          Importes en{" "}
          {report.cost.unit === "openrouter_credits" ? "créditos de OpenRouter" : report.cost.unit},
          tal como los reporta el proveedor. No es una factura ni está convertido a otra moneda.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Metric
            label="Créditos conocidos"
            value={report.cost.knownTotal ?? "Desconocido"}
            state={state}
            hint={
              report.cost.knownTotal === null
                ? "Ninguna llamada reportó su coste"
                : "Suma exacta de los costes reportados"
            }
          />
          <Metric label="Llamadas con coste conocido" value={count(report.cost.callsWithCost)} />
          <Metric
            label="Llamadas con coste desconocido"
            value={count(report.cost.callsWithoutCost)}
            hint="No se cuentan como cero"
          />
        </div>
        <p className="text-sm text-foreground" data-testid="cost-completeness">
          {COMPLETENESS_LABEL[report.cost.completeness]}
        </p>
        {report.cost.explicitZeroCostCalls > 0 ? (
          <p className="text-xs text-muted-foreground">
            {count(report.cost.explicitZeroCostCalls)} llamadas reportaron un coste de cero de forma
            explícita.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function QuotaPanel({ report }: { report: UsageReport }) {
  const quota = report.quota;
  if (!quota.configured) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cuota de análisis</CardTitle>
          <CardDescription>No hay un límite configurado para esta organización.</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Las unidades se registran igualmente. Sin límite configurado no hay remanente que
            mostrar.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Cuota de análisis</CardTitle>
        <CardDescription>
          Plan {quota.plan ?? "sin plan"} · límite mensual de{" "}
          {count(quota.monthlyAnalysisLimit ?? 0)} análisis
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Metric label="Unidades consumidas" value={count(quota.unitsConsumed)} />
          <Metric
            label="Unidades liberadas"
            value={count(quota.unitsReleased)}
            hint="Sin llamada al proveedor"
          />
          <Metric label="Análisis contados" value={count(quota.analysesCounted)} />
          {quota.remainingAvailable && quota.remaining !== null ? (
            <Metric label="Unidades restantes" value={count(quota.remaining)} />
          ) : (
            <Metric
              label="Unidades restantes"
              value="No disponible"
              hint="Requiere el enforcement activado y un límite"
            />
          )}
        </div>
        <div className="grid gap-2 text-sm">
          <p className="text-foreground">
            Enforcement:{" "}
            <span className="font-medium">
              {quota.enforcementEnabled ? "activado" : "desactivado"}
            </span>
            {quota.enforcementEnabled ? null : (
              <span className="text-muted-foreground">
                {" "}
                · las unidades se cuentan, pero no se rechaza ningún análisis
              </span>
            )}
          </p>
          <p className="text-foreground">
            Reconciliación:{" "}
            <span className="font-medium">
              {quota.reconciled ? "conciliado" : "no conciliado, revisar"}
            </span>
            <span className="text-muted-foreground">
              {" "}
              · consumidas + liberadas = análisis contados
            </span>
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

/** Explains a month without activity, so an empty report is not mistaken for a failure. */
export function NoActivityNotice({ period }: { period: string }) {
  return (
    <div
      role="status"
      className="rounded-xl border border-border bg-surface-elevated px-5 py-4 text-sm text-muted-foreground"
    >
      Sin actividad registrada en {period} (UTC). Las cifras en cero significan que no hubo análisis
      ni llamadas en ese periodo.
    </div>
  );
}
