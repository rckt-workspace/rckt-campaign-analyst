import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { CampaignFormFields } from "@/lib/campaign-metrics";

type FieldKey = keyof CampaignFormFields;

const GROUPS: { title: string; fields: { key: FieldKey; label: string; placeholder: string }[] }[] =
  [
    {
      title: "Información general",
      fields: [
        { key: "campaign", label: "Nombre de campaña", placeholder: "Lanzamiento RCKT Growth" },
        { key: "channel", label: "Canal", placeholder: "Meta Ads" },
        { key: "objective", label: "Objetivo", placeholder: "Ventas" },
        { key: "period", label: "Periodo", placeholder: "1 al 15 de agosto de 2026" },
      ],
    },
    {
      title: "Inversión y exposición",
      fields: [
        { key: "investment", label: "Inversión", placeholder: "$4.850.000" },
        { key: "impressions", label: "Impresiones", placeholder: "410.000" },
        { key: "reach", label: "Alcance", placeholder: "245.000" },
        { key: "frequency", label: "Frecuencia", placeholder: "1,67" },
      ],
    },
    {
      title: "Interacción",
      fields: [
        { key: "clicks", label: "Clics", placeholder: "8.610" },
        { key: "ctr", label: "CTR", placeholder: "2,10%" },
        { key: "cpc", label: "CPC", placeholder: "$563" },
      ],
    },
    {
      title: "Conversión",
      fields: [
        { key: "leads", label: "Leads", placeholder: "522" },
        { key: "cpl", label: "CPL", placeholder: "$9.291" },
        { key: "conversions", label: "Ventas / Conversiones", placeholder: "94" },
        { key: "revenue", label: "Ingresos", placeholder: "$11.960.000" },
        { key: "roas", label: "ROAS", placeholder: "2,47" },
      ],
    },
  ];

export function QuickForm({
  values,
  onChange,
}: {
  values: CampaignFormFields;
  onChange: (key: FieldKey, value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-5">
      {GROUPS.map((group) => (
        <fieldset key={group.title} className="min-w-0">
          <legend className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            {group.title}
          </legend>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {group.fields.map((field) => (
              <div key={field.key} className="min-w-0">
                <Label
                  htmlFor={`field-${field.key}`}
                  className="text-xs font-medium text-muted-foreground"
                >
                  {field.label}
                </Label>
                <Input
                  id={`field-${field.key}`}
                  value={values[field.key]}
                  onChange={(event) => onChange(field.key, event.target.value)}
                  placeholder={field.placeholder}
                  autoComplete="off"
                  className="mt-1.5 w-full rounded-lg border-border bg-background/60 text-sm transition-colors duration-200 placeholder:text-muted-foreground/50 focus-visible:border-primary"
                />
              </div>
            ))}
          </div>
        </fieldset>
      ))}

      <fieldset className="min-w-0">
        <legend className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
          Información adicional
        </legend>
        <Textarea
          id="field-additionalContext"
          value={values.additionalContext}
          onChange={(event) => onChange("additionalContext", event.target.value)}
          placeholder="Contexto del cliente, restricciones, aprendizajes previos…"
          className="mt-3 min-h-[96px] w-full resize-y rounded-lg border-border bg-background/60 text-sm transition-colors duration-200 placeholder:text-muted-foreground/50 focus-visible:border-primary"
        />
      </fieldset>
    </div>
  );
}
