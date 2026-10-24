import { useState, type FormEvent } from "react";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { Sparkles } from "lucide-react";
import { AppHeader } from "@/components/campaign/AppHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getAccount, signIn } from "@/lib/auth.functions";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>) => ({
    // TanStack parses URL values: "1" arrives as the number 1. Booleans written in code arrive as true.
    expired: [1, "1", true, "true"].includes(search["expired"] as never),
  }),
  beforeLoad: async () => {
    // Already signed in: go straight to the application.
    const account = await getAccount();
    if (account) throw redirect({ to: "/" });
  },
  head: () => ({
    meta: [{ title: "Iniciar sesión — RCKT Campaign Analyst" }],
  }),
  component: LoginPage,
});

const DEFAULT_MESSAGE = "No pudimos iniciar sesión en este momento. Intenta de nuevo más tarde.";
const MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "Correo o contraseña incorrectos.",
};

function LoginPage() {
  const { expired } = Route.useSearch();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await signIn({ data: { email, password } });
      await navigate({ to: "/" });
    } catch (caught) {
      const code = caught instanceof Error ? caught.message : "";
      setError(MESSAGES[code] ?? DEFAULT_MESSAGE);
      setPassword("");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <AppHeader />
      <main className="mx-auto flex max-w-md flex-col px-5 pb-16 pt-12 md:pt-20">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-primary text-primary-foreground">
            <span className="text-sm font-bold tracking-tight">R</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Accede a tu espacio</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Análisis ejecutivo de campañas para tu organización.
          </p>
        </div>

        {expired ? (
          <div role="status" className="mb-5 rounded-xl border border-border bg-surface-elevated px-4 py-3 text-sm text-foreground">
            Tu sesión expiró. Inicia sesión de nuevo para continuar.
          </div>
        ) : null}

        <form
          onSubmit={handleSubmit}
          className="space-y-5 rounded-2xl border border-border bg-surface-elevated p-6 shadow-sm"
          aria-describedby={error ? "login-error" : undefined}
        >
          <div className="space-y-2">
            <Label htmlFor="login-email">Correo electrónico</Label>
            <Input
              id="login-email"
              name="email"
              type="email"
              autoComplete="email"
              required
              maxLength={320}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={pending}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="login-password">Contraseña</Label>
            <Input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={200}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={pending}
            />
          </div>

          {error ? (
            <p id="login-error" role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}

          <Button type="submit" className="w-full gap-2 rounded-xl" disabled={pending || !email || !password}>
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            {pending ? "Ingresando…" : "Iniciar sesión"}
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          El acceso lo asigna RCKT. Si no tienes credenciales, solicítalas a tu contacto en RCKT.
        </p>
      </main>
    </div>
  );
}
