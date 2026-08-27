import { useState } from "react";
import { ClipboardList, FileText, FlaskConical, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

const PLACEHOLDER = `Campaña: Lanzamiento Producto X
Canal: Meta Ads
Inversión: $4.850.000
Impresiones: 410.000
Clics: 8.610
CTR: 2,10%
Ventas: 94
Ingresos: $11.960.000
ROAS: 2,47`;

type InputMode = "paste" | "form";

export function CampaignInputPanel() {
  const [mode, setMode] = useState<InputMode>("paste");
  const [value, setValue] = useState("");

  return (
    <section className="panel flex flex-col p-5 md:p-6">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-border bg-surface-elevated">
          <ClipboardList className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold tracking-tight text-foreground">
            Datos de campaña
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Ingresa los resultados de una campaña para comenzar el análisis.
          </p>
        </div>
      </div>

      <div
        role="tablist"
        aria-label="Modo de ingreso de datos"
        className="mt-5 inline-flex w-full gap-1 rounded-xl border border-border bg-surface-elevated p-1 sm:w-auto"
      >
        <ModeTab
          active={mode === "paste"}
          icon={<FileText className="h-3.5 w-3.5" aria-hidden="true" />}
          label="Pegar métricas"
          onClick={() => setMode("paste")}
        />
        <ModeTab
          active={mode === "form"}
          icon={<Wand2 className="h-3.5 w-3.5" aria-hidden="true" />}
          label="Formulario rápido"
          onClick={() => setMode("form")}
        />
      </div>

      {mode === "paste" ? (
        <div className="mt-5">
          <Textarea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={PLACEHOLDER}
            aria-label="Métricas de campaña"
            className="min-h-[300px] resize-y rounded-xl border-border bg-background/60 font-mono text-[13px] leading-relaxed transition-colors duration-200 placeholder:text-muted-foreground/60 focus-visible:border-primary md:min-h-[340px]"
          />
        </div>
      ) : (
        <div className="mt-5 grid min-h-[300px] place-items-center rounded-xl border border-dashed border-border bg-background/40 px-6 text-center md:min-h-[340px]">
          <div>
            <p className="text-sm font-medium text-foreground">Formulario rápido</p>
            <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
              Aquí podrás capturar cada métrica en campos individuales.
            </p>
          </div>
        </div>
      )}

      <div className="mt-5 flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
        <Button
          variant="outline"
          disabled
          className="justify-center gap-2 rounded-xl border-border bg-surface-elevated text-muted-foreground"
        >
          <FlaskConical className="h-4 w-4" aria-hidden="true" />
          Cargar ejemplo
        </Button>
        <Button
          disabled
          className="justify-center rounded-xl px-6 transition-colors duration-200"
        >
          Analizar campaña
        </Button>
      </div>
    </section>
  );
}

function ModeTab({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`inline-flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition-colors duration-200 sm:flex-none ${
        active
          ? "bg-primary text-primary-foreground"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
