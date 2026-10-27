# Integración con `main` de Lovable: decisiones y pendientes

Estado: merge local `029a841` sobre `chore/docker-compose-node-production`. Sin push. El PR #1 sigue en Draft.

## 1. Decisiones

| Tema | Decisión | Motivo |
|---|---|---|
| Ubicación de migraciones SQL portables | `supabase/migrations/` (oficial) | Acordada con el responsable |
| `supabase/config.toml` (generado por Lovable) | Conservado, con advertencia documentada | Lovable lo necesita; ver sección 3 |
| Drizzle (`drizzle/`) | Solo para componentes generados por Lovable | Nuestro esquema empresarial es Alembic + SQL portable |
| Sincronía de esquemas | SQL portable y Alembic deben describir el mismo estado (verificado con `pg_dump -s` y `supabase/verification.sql`) | Evitar dos modelos empresariales divergentes |
| RLS | Activo en todas las tablas empresariales, sin políticas | Acceso cerrado hasta validar identidad real |
| GRANT para roles de API | No concedidos a propósito | Ver sección 3; se añadirán con migraciones futuras |
| `@supabase/supabase-js` | 2.117.0 fijado | 2.118.0 aún no cumple la regla de dos semanas; reevaluar el 29/oct/2026 |
| `@lovable.dev/vite-tanstack-config` | 2.26.5 | Configuración de la plataforma; la vista previa depende de ella |
| `attachSupabaseAuth` (middleware de Lovable) | No registrado en `src/start.ts` | Enviaría el token de acceso desde el navegador a cada server function |
| Clientes generados de Lovable | Presentes pero sin uso | `client.server.ts` usa `service_role` (salta RLS); ningún código de la aplicación los importa |
| `.env` de `main` | No incorporado; sin cambios en `main` | Contiene configuración de un proyecto; ver sección 3 |

## 2. Verificaciones del commit `029a841`

- Padres: `8803666` (nuestra rama) y `67e2b11` (`origin/main`).
- `git fsck` sin errores. Sin marcadores de conflicto en archivos de texto.
- `bun install --frozen-lockfile` acepta el lockfile regenerado.
- Rutas protegidas: `src/routes/index.tsx` y `src/routes/login.tsx` usan `getAccount` (sesión validada con `getClaims` y membresía consultada en FastAPI).
- Análisis: `src/lib/campaign-api.functions.ts` valida la sesión y reenvía el token a `POST /api/v1/organizations/{id}/analyses` (con `Idempotency-Key`), que exige membresía. El endpoint `campaigns/analyze` está retirado (410). No existe ruta anónima de análisis.
- Ningún archivo fuera de `src/integrations/` importa los módulos generados por Lovable.
- `.env`: no existía un `.env` local antes del merge; el que se eliminó fue la copia que el propio merge trajo de `main`.

## 3. Pendientes si se fusiona el PR en `main`

1. **`.env` versionado en `main`** (commit `3b79772` y posteriores). Contiene identificador y URL del proyecto de Lovable Cloud, y una clave publicable. La clave publicable está diseñada para el navegador, pero el archivo no debe versionarse. Retirarlo de `main` requiere coordinar con quien administra el proyecto en Lovable. Los valores que contiene siguen en el historial de `main`.
2. **Políticas y GRANT.** Las migraciones de fundación y de campañas activan RLS sin políticas ni GRANT. La regla de `AGENTS.md` pide GRANT y RLS en la misma migración para cada tabla nueva. Nuestro estado es deliberadamente más cerrado: los roles de API no ven filas. Cuando el flujo de identidad esté validado, las políticas y los GRANT necesarios entrarán en migraciones futuras, no editando las ya aplicadas.
3. **Dos herramientas de migración sobre la misma base.** Drizzle (Lovable) y Alembic + SQL portable (nosotros). Hay que definir cuál gestiona cada grupo de tablas y cómo se reconcilian si una toca la misma tabla.
4. **`supabase/config.toml` y la carpeta `supabase/migrations/`.** Lovable conserva `project_id` en el archivo. Antes de ejecutar cualquier comando del CLI de Supabase (`db push`, `migration up`) sobre esa base, revisar el historial del CLI: las migraciones ejecutadas a mano no aparecen en él.
5. **`drizzle-orm` y `drizzle-kit` declarados como `"0.x"`.** Es un rango flotante. El lockfile fija 0.45.4 y 0.31.11; cualquier reinstalación que ignore el lockfile puede cambiar la versión. Conviene un rango explícito.
6. **`@supabase/supabase-js`.** Lovable declara `^2.118.0`; nosotros fijamos 2.117.0. Al cumplirse la regla de dos semanas (29/oct/2026), decidir si se actualiza.
7. **Espacios al final en archivos generados por Lovable** (`src/integrations/supabase/auth-middleware.ts`) y una línea en blanco al final de `AGENTS.md`. Los avisos de `git diff --check` provienen de esos archivos. No se editan.
8. **Validación en vivo.** El flujo real de login contra el proyecto de Lovable Cloud no está verificado. Hay que ejecutar `scripts/supabase-live-check.py` con las credenciales del proyecto, siguiendo `docs/PHASE2_AUTH_LIVE.md`.
9. **Aprovisionamiento.** Las membresías de usuarios reales se conceden con la CLI de operador. Ningún usuario recién registrado tiene acceso por defecto.
