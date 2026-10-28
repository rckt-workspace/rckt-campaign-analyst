import { useCallback, useEffect, useRef, useState } from "react";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import { AppHeader } from "@/components/campaign/AppHeader";
import { AuditPanel, type AuditState } from "@/components/admin/AuditPanel";
import {
  CostPanel,
  NoActivityNotice,
  QuotaPanel,
  UsageSummary,
} from "@/components/admin/UsagePanels";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ADMIN_ERRORS, adminMessage as messageFor } from "@/lib/admin-errors";
import { currentPeriod, recentPeriods, type UsageReport } from "@/lib/admin-usage";
import { getAccount, signOut } from "@/lib/auth.functions";
import {
  getAdminOrganizations,
  getAuditEvents,
  getUsageReport,
  type AdminOrganization,
} from "@/lib/admin.functions";

/**
 * Administrative screen for the owners of an organization. Only the owner of the selected organization can read its
 * consumption and audit trail, and FastAPI checks that again on every call. Choosing an organization or a period only
 * changes what the screen will ask for: it never asks for the usage report by itself.
 */

function codeOf(error: unknown): string {
  return error instanceof Error ? error.message : ADMIN_ERRORS.UNAVAILABLE;
}

type UsageState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; report: UsageReport }
  | { status: "error"; message: string };

const IDLE_AUDIT: AuditState = {
  status: "idle",
  events: [],
  nextCursor: null,
  errorMessage: null,
  loadingMore: false,
};

export const Route = createFileRoute("/administracion")({
  beforeLoad: async () => {
    // Protected: without a valid session the page is never rendered.
    const account = await getAccount();
    if (!account) throw redirect({ to: "/login", search: { expired: false } });
    return { account };
  },
  loader: async () => {
    // Only the list of organizations the user owns. Nothing audited is requested here.
    try {
      const { organizations } = await getAdminOrganizations();
      return { organizations, error: null as string | null };
    } catch (error) {
      return { organizations: [] as AdminOrganization[], error: codeOf(error) };
    }
  },
  head: () => ({
    meta: [{ title: "Administración — RCKT Campaign Analyst" }],
  }),
  component: AdministrationPage,
});

function AdministrationPage() {
  const { account } = Route.useRouteContext();
  const { organizations, error: loadError } = Route.useLoaderData();
  const navigate = useNavigate();

  const [organizationId, setOrganizationId] = useState<string>(organizations[0]?.id ?? "");
  const [periods] = useState<string[]>(() => recentPeriods(new Date(), 12));
  const [period, setPeriod] = useState<string>(() => currentPeriod(new Date()));
  const [usage, setUsage] = useState<UsageState>({ status: "idle" });
  const [audit, setAudit] = useState<AuditState>(IDLE_AUDIT);

  // Stale answers are ignored: a response for another organization or period never overwrites the current one.
  const usageSequence = useRef(0);
  const auditSequence = useRef(0);
  const usageInFlight = useRef(false);

  const handleSessionExpired = useCallback(async () => {
    await navigate({ to: "/login", search: { expired: true } });
  }, [navigate]);

  const loadAuditPage = useCallback(
    async (targetOrganization: string, cursor?: number) => {
      const sequence = ++auditSequence.current;
      setAudit((previous) =>
        cursor === undefined
          ? { ...IDLE_AUDIT, status: "loading" }
          : { ...previous, loadingMore: true, errorMessage: null },
      );
      try {
        const page = await getAuditEvents({
          data: { organizationId: targetOrganization, cursor, limit: 10 },
        });
        if (sequence !== auditSequence.current) return;
        setAudit((previous) => ({
          status: "ready",
          events: cursor === undefined ? page.items : [...previous.events, ...page.items],
          nextCursor: page.nextCursor,
          errorMessage: null,
          loadingMore: false,
        }));
      } catch (error) {
        if (sequence !== auditSequence.current) return;
        const code = codeOf(error);
        if (code === ADMIN_ERRORS.SESSION_EXPIRED) {
          await handleSessionExpired();
          return;
        }
        setAudit((previous) => ({
          ...previous,
          status: "error",
          errorMessage: messageFor(code),
          loadingMore: false,
        }));
      }
    },
    [handleSessionExpired],
  );

  // The first page of the trail is a read: it is not audited, so it may load when the screen opens.
  useEffect(() => {
    if (organizationId) void loadAuditPage(organizationId);
    // Only the initial organization; later changes are handled by chooseOrganization.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const chooseOrganization = (next: string) => {
    setOrganizationId(next);
    usageSequence.current += 1;
    usageInFlight.current = false;
    setUsage({ status: "idle" });
    void loadAuditPage(next);
  };

  /** The only way to request the usage report: one explicit press. Each press is one audited access. */
  const consultUsage = async () => {
    if (usageInFlight.current || !organizationId) return;
    usageInFlight.current = true;
    const sequence = ++usageSequence.current;
    setUsage({ status: "loading" });
    try {
      const report = await getUsageReport({ data: { organizationId, period } });
      if (sequence !== usageSequence.current) return;
      setUsage({ status: "ready", report });
      // The access just recorded is part of the trail now, so the first page is read again.
      void loadAuditPage(organizationId);
    } catch (error) {
      if (sequence !== usageSequence.current) return;
      const code = codeOf(error);
      if (code === ADMIN_ERRORS.SESSION_EXPIRED) {
        await handleSessionExpired();
        return;
      }
      setUsage({ status: "error", message: messageFor(code) });
    } finally {
      if (sequence === usageSequence.current) usageInFlight.current = false;
    }
  };

  const handleSignOut = async () => {
    await signOut();
    await navigate({ to: "/login", search: { expired: false } });
  };

  const hasOwnership = organizations.length > 0;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={account.email} onSignOut={() => void handleSignOut()} />
      <main className="mx-auto grid max-w-7xl gap-6 px-5 pb-16 pt-8 md:px-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">
              Administración
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">Consumo y auditoría de campañas</p>
          </div>
          <Link
            to="/"
            className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            Volver al análisis
          </Link>
        </div>

        {loadError ? (
          <div
            role="alert"
            className="rounded-xl border border-border bg-surface-elevated px-5 py-4 text-sm text-destructive"
          >
            {messageFor(loadError)}
          </div>
        ) : null}

        {!hasOwnership && !loadError ? (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Sin acceso de administración</CardTitle>
              <CardDescription>
                Esta pantalla es solo para owners de una organización. Tu cuenta no tiene ninguna
                membresía de owner.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Si necesitas consultar el consumo de una organización, pide a su owner que lo haga,
                o contacta con RCKT.
              </p>
            </CardContent>
          </Card>
        ) : null}

        {hasOwnership ? (
          <>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Consulta</CardTitle>
                <CardDescription>
                  Elige la organización y el mes (UTC). Consultar el consumo queda registrado en la
                  auditoría.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_12rem_auto] sm:items-end">
                <label className="grid gap-1.5 text-sm">
                  <span className="text-muted-foreground">Organización</span>
                  <select
                    data-testid="admin-organization"
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground"
                    value={organizationId}
                    onChange={(event) => chooseOrganization(event.target.value)}
                  >
                    {organizations.map((organization) => (
                      <option key={organization.id} value={organization.id}>
                        {organization.name} ({organization.slug})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="grid gap-1.5 text-sm">
                  <span className="text-muted-foreground">Periodo (UTC)</span>
                  <select
                    data-testid="admin-period"
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground"
                    value={period}
                    onChange={(event) => {
                      // Changing the period selects it; it does not request the report.
                      usageSequence.current += 1;
                      usageInFlight.current = false;
                      setUsage({ status: "idle" });
                      setPeriod(event.target.value);
                    }}
                  >
                    {periods.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  type="button"
                  data-testid="consult-usage"
                  onClick={() => void consultUsage()}
                  disabled={usage.status === "loading"}
                >
                  {usage.status === "ready" ? "Actualizar consumo" : "Consultar consumo"}
                </Button>
              </CardContent>
            </Card>

            {usage.status === "idle" ? (
              <p className="text-sm text-muted-foreground">
                Pulsa «Consultar consumo» para ver el informe del periodo seleccionado.
              </p>
            ) : null}
            {usage.status === "loading" ? (
              <p role="status" className="text-sm text-muted-foreground">
                Consultando el consumo…
              </p>
            ) : null}
            {usage.status === "error" ? (
              <div
                role="alert"
                className="rounded-xl border border-border bg-surface-elevated px-5 py-4 text-sm text-destructive"
              >
                {usage.message}
              </div>
            ) : null}
            {usage.status === "ready" ? (
              <section
                data-testid="usage-report"
                className="grid gap-6"
                aria-label="Informe de consumo"
              >
                {!usage.report.hasActivity ? (
                  <NoActivityNotice period={usage.report.period} />
                ) : null}
                <UsageSummary report={usage.report} />
                <CostPanel report={usage.report} />
                <QuotaPanel report={usage.report} />
              </section>
            ) : null}

            <AuditPanel
              state={audit}
              onLoadMore={() => {
                if (audit.nextCursor !== null && organizationId)
                  void loadAuditPage(organizationId, audit.nextCursor);
              }}
            />
          </>
        ) : null}
      </main>
    </div>
  );
}
