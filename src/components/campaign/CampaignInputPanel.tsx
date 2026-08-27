import { useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ClipboardList,
  FileText,
  FlaskConical,
  Info,
  Wand2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { QuickForm } from "@/components/campaign/QuickForm";
import {
  EMPTY_FORM,
  EXAMPLE_FORM,
  EXAMPLE_RAW,
  countFilledFields,
  isEmptyInput,
  normalizeCampaign,
  type CampaignFormFields,
  type NormalizedCampaign,
} from "@/lib/campaign-metrics";

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
  const [rawValue, setRawValue] = useState("");
  const [form, setForm] = useState<CampaignFormFields>(EMPTY_FORM);
  const [emptyNotice, setEmptyNotice] = useState(false);
  const [prepared, setPrepared] = useState<NormalizedCampaign | null>(null);

  const normalized = useMemo(
    () => normalizeCampaign(mode, rawValue, form),
    [mode, rawValue, form],
  );
  const empty = isEmptyInput(mode, rawValue, form);
  const filled = countFilledFields(normalized);
  const canAnalyze = !empty && filled >= 2;
  const scarce = !empty && filled > 0 && filled < 5;

  const handleFormChange = (key: keyof CampaignFormFields, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setEmptyNotice(false);
    setPrepared(null);
  };

  const loadExample = () => {
    setRawValue(EXAMPLE_RAW);
    setForm(EXAMPLE_FORM);
    setEmptyNotice(false);
    setPrepared(null);
  };

  const handleAnalyze = () => {
    if (empty) {
      setEmptyNotice(true);
      setPrepared(null);
      return;
    }
    setEmptyNotice(false);
    setPrepared(normalized);
  };

  return (
    <section className="panel flex min-w-0 flex-col p-5 md:p-6">
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
            value={rawValue}
            onChange={(event) => {
              setRawValue(event.target.value);
              setEmptyNotice(false);
              setPrepared(null);
            }}
            placeholder={PLACEHOLDER}
            aria-label="Métricas de campaña"
            className="min-h-[300px] w-full resize-y rounded-xl border-border bg-background/60 font-mono text-[13px] leading-relaxed transition-colors duration-200 placeholder:text-muted-foreground/60 focus-visible:border-primary md:min-h-[340px]"
          />
        </div>
      ) : (
        <div className="mt-5">
          <QuickForm values={form} onChange={handleFormChange} />
        </div>
      )}

      <div aria-live="polite" className="mt-4 flex flex-col gap-2 empty:mt-0">
        {emptyNotice ? (
          <Notice
            tone="warn"
            icon={<AlertCircle className="h-4 w-4" aria-hidden="true" />}
            text="Agrega algunas métricas para comenzar el análisis."
          />
        ) : null}
        {scarce ? (
          <Notice
            tone="info"
            icon={<Info className="h-4 w-4" aria-hidden="true" />}
            text="Con más métricas podremos generar un análisis más completo."
          />
        ) : null}
        {prepared ? (
          <Notice
            tone="ok"
            icon={<CheckCircle2 className="h-4 w-4" aria-hidden="true" />}
            text={`Datos preparados: ${filled} campos reconocidos y normalizados. El análisis se habilitará en la siguiente etapa.`}
          />
        ) : null}
      </div>

      <div className="mt-5 flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
        <Button
          type="button"
          variant="outline"
          onClick={loadExample}
          className="justify-center gap-2 rounded-xl border-border bg-surface-elevated text-foreground transition-colors duration-200 hover:border-primary/50"
        >
          <FlaskConical className="h-4 w-4 text-primary" aria-hidden="true" />
          Cargar ejemplo
        </Button>
        <Button
          type="button"
          onClick={handleAnalyze}
          disabled={!canAnalyze}
          className="justify-center rounded-xl px-6 transition-colors duration-200"
        >
          Analizar campaña
        </Button>
      </div>
    </section>
  );
}

function Notice({
  tone,
  icon,
  text,
}: {
  tone: "warn" | "info" | "ok";
  icon: React.ReactNode;
  text: string;
}) {
  const tones = {
    warn: "border-destructive/40 bg-destructive/10 text-foreground [&>svg]:text-destructive",
    info: "border-border bg-surface-elevated text-muted-foreground [&>svg]:text-primary",
    ok: "border-primary/40 bg-primary/10 text-foreground [&>svg]:text-primary",
  } as const;

  return (
    <div
      className={`flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm ${tones[tone]}`}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <p className="min-w-0">{text}</p>
    </div>
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
