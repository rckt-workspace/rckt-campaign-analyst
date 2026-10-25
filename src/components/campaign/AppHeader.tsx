import { LogOut, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

export function AppHeader({
  email,
  onSignOut,
}: {
  email?: string | null;
  onSignOut?: () => void;
}) {
  return (
    <header className="sticky top-0 z-20 border-b border-border/80 bg-background/85 backdrop-blur-xl">
      <div className="mx-auto grid max-w-7xl grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-5 py-4 sm:flex sm:justify-between md:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground">
            <span className="text-xs font-bold tracking-tight">R</span>
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-bold tracking-[0.2em] text-foreground">RCKT</p>
            <p className="truncate text-xs text-muted-foreground">Campaign Analyst</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="flex items-center gap-2 rounded-full border border-border bg-surface-elevated px-3 py-1.5">
            <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
            <span className="text-[11px] font-medium tracking-wide text-muted-foreground">
              AI Campaign Intelligence
            </span>
          </div>
          {onSignOut ? (
            <div className="flex items-center gap-1">
              {email ? (
                <span className="hidden max-w-[10rem] truncate text-xs text-muted-foreground md:inline" title={email}>
                  {email}
                </span>
              ) : null}
              <Button type="button" variant="ghost" size="sm" className="gap-1.5 rounded-full" onClick={onSignOut}>
                <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                <span className="text-xs">Cerrar sesión</span>
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
