import { BarChart3, Radar } from "lucide-react";

const SECTIONS = [
  "Estado general",
  "Resumen ejecutivo",
  "Principales hallazgos",
  "Recomendaciones",
  "Información adicional",
];

export function ExecutiveReportPanel() {
  return (
    <section className="panel flex flex-col p-5 md:p-6">
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

      <div className="mt-5 flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-background/40 px-6 py-14 text-center">
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
    </section>
  );
}
