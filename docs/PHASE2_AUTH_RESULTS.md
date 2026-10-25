# Fase 2.2: resultados de verificación

Fecha: 24/oct/2026. Rama local `chore/docker-compose-node-production`. Sin llamadas a OpenRouter de pago. Sin conexión a un proyecto Supabase real.

## Pruebas automatizadas

| Suite | Entorno | Resultado |
|---|---|---|
| Backend (`pytest`) | Python 3.11, PostgreSQL 16 de desarrollo (`rckt_test`) | **93 passed** |
| Backend (`pytest`) | Python 3.12 en contenedor `python:3.12-slim`, dentro de Compose, PostgreSQL 16 | **93 passed** |
| TypeScript (`tsc --noEmit`) | Frontend | **exit 0** |
| Build de producción Node | `RCKT_DEPLOY_TARGET=node` | OK, genera `.output/server/index.mjs` |
| Playwright E2E | Build Node + FastAPI + emisor JWT simulado + proveedor OpenRouter simulado + PostgreSQL `rckt_e2e_test` recreada en cada ejecución | **17 passed** (1 de setup, 8 de autenticación, 8 de campañas) |

Cobertura backend de la tarea 7 (ver `tests/test_tokens.py`, `tests/test_authz_api.py`, `tests/test_cli.py`, `tests/test_api.py`):

| # | Caso | Prueba |
|---|---|---|
| 1 | Inicio de sesión simulado | E2E `setup` y `auth.spec.ts` |
| 2 | JWT válido | `test_valid_es256_token_is_accepted`, `test_valid_rs256_token_is_accepted` |
| 3 | JWT expirado | `test_expired_token_is_rejected`, `test_me_rejects_expired_token` |
| 4 | Firma inválida | `test_forged_signature_is_rejected`, `test_forged_token_cannot_reach_analysis` |
| 5 | Issuer o audience incorrectos | `test_wrong_issuer_is_rejected`, `test_wrong_audience_is_rejected` |
| 6 | Token ausente | `test_me_requires_a_token`, `test_analysis_requires_identity_before_validation_or_cost` |
| 7 | Usuario sin membresía | `test_user_without_membership_gets_not_found`, E2E `usuario sin membresía` |
| 8 | Usuario autorizado | `test_analyst_can_analyze`, `test_owner_can_analyze` |
| 9 | Restricción de `client_viewer` | `test_client_viewer_can_read_but_cannot_generate_paid_analysis`, E2E `client_viewer` |
| 10 | Acceso cruzado entre organizaciones | `test_member_of_one_organization_cannot_analyze_another`, `test_memberships_scope_access_to_their_own_organizations` |
| 11 | Análisis anónimo | `test_anonymous_analysis_is_refused_and_never_reaches_the_provider` |
| 12 | Sesión expirada | E2E `sesión expirada y revocada`, `test_expired_session_cannot_analyze` |
| 13 | Ausencia de secretos en el navegador | E2E `expectNoSecrets`, cookies `HttpOnly`, storage sin tokens, bundle sin claves |
| 14 | Compatibilidad con Compose | Stack verificado abajo; Compose se valida con `docker compose config` |

Además: `test_hs256_token_is_rejected_even_with_known_kid` (confusión de algoritmos), `test_alg_none_is_rejected`, `test_unreachable_jwks_endpoint_is_an_outage_not_an_auth_decision` (503, no 401), `test_missing_identity_configuration_fails_closed` y `test_missing_database_fails_closed`.

## Compose

- `docker-compose.yml` (base): solo `backend` y `frontend`. Verificado con `config --services`: no contiene PostgreSQL.
- `compose.dev.yml` (desarrollo, combinado explícitamente con `-f`): `db`, `migrate`, `tests` (perfil `test`).
- Stack levantado con `docker compose -f docker-compose.yml -f compose.dev.yml up -d`:
  - `db`: healthy, publicado solo en `127.0.0.1:55432` (pruebas locales).
  - `migrate`: exit 0.
  - `backend`: healthy. `/readyz` 200. `/healthz` 200. Análisis anónimo → 401. Sin puerto publicado.
  - `frontend`: healthy en `127.0.0.1:3100`. `/login` responde 200 tras la normalización de la URL de TanStack. `/` sin sesión redirige a `/login`.
- El volumen `rckt-campaign-analyst_rckt-pgdata` se conservó; no se reinicializó PostgreSQL.
- El puerto 3000 lo ocupan procesos ajenos a este proyecto; no se tocaron.

Estado de configuración de Supabase en este entorno: `SUPABASE_URL` vacía. El frontend registra "Supabase server configuration is incomplete" y no abre la aplicación (fail closed).

## Errores encontrados y corregidos durante la verificación

1. **Firma de `kid` desconocido**: un `kid` falso enviado por un atacante producía 503. Se distingue ahora el fallo de conexión al JWKS (503) del `kid` desconocido (401).
2. **Helper de pruebas de tokens**: no podía firmar con un `kid` desconocido. Corregido para aceptar clave explícita.
3. **Cookies de sesión legibles por JavaScript**: `@supabase/ssr` escribe cookies sin `HttpOnly`. Las cookies se fuerzan a `HttpOnly` y `SameSite=Lax`. Lo detectó la prueba E2E de cookies.
4. **Validación de parámetros en TanStack**: `?expired=1` llega como número. Corregido.
5. **Forma de la llamada de análisis**: la server function recibía un argumento desenvuelto. Corregido.
6. **Importaciones de servidor en el bundle cliente**: movidas dentro de los handlers, como el código original. Confirmado: ningún rastro de Supabase en el bundle del navegador.
7. **Migraciones**: el preparador de la base E2E no encontraba el paquete `app`. Corregido.
8. **`bun run` no pasa `.env` a procesos hijos**: el preparador y la configuración de Playwright leen `E2E_DATABASE_URL` del entorno o del `.env` del repositorio.
9. **JSON en ESM**: `identities.json` se lee con `fs`, porque Node exige `type: json`.
10. **Puerto 3100 ocupado por un contenedor del propio proyecto** de una verificación anterior: detenido antes de la ejecución E2E.

## Estado de autenticación: real frente a simulada

- **Simulada (verificada):** emisor con claves ES256 ficticias que publica JWKS y firma tokens reales. Los tokens los verifica FastAPI con PyJWT. El frontend usa el SDK oficial `@supabase/ssr` 0.12.7 y `@supabase/supabase-js` 2.117.0 contra ese emisor.
- **Real (no verificada):** requiere un proyecto Supabase con credenciales. Pasos en `docs/PHASE2_AUTH.md`.

## Pendientes antes de producción

- Probar el flujo completo contra un proyecto Supabase real, con sus claves de firma asimétricas.
- Ejecutar el CI en GitHub (sigue bloqueado por facturación) con los nuevos jobs, incluido `e2e` con PostgreSQL.
- Definir el proceso de aprovisionamiento de cuentas y membresías con RCKT.
- Selector de organización en la interfaz para usuarios con varias organizaciones.
- Límites de intentos de login y política de sesiones según el plan de Supabase.
- Revocación inmediata de tokens de acceso (hoy: expiración corta).
- RLS en PostgreSQL (etapa 2.7).

## Fase 2.2B: integración real de Supabase Auth

**Estado: no verificada contra un proyecto Supabase real.** No hay un proyecto dedicado a RCKT configurado en este entorno, y no se usaron credenciales de otros proyectos (en esta máquina hay una instancia local de Supabase CLI de otro proyecto; no se tocó). Procedimiento para activarla: `docs/PHASE2_AUTH_LIVE.md`.

Lo que sí se verificó sin credenciales reales:

| Verificación | Resultado |
|---|---|
| E2E Playwright (identidad simulada, proveedor simulado) | **18 passed** (setup, 9 de autenticación, 8 de campañas) |
| Caché: respuestas autenticadas con `Cache-Control: no-store` | Verificado en E2E |
| Cookies de sesión: `HttpOnly`, `Secure`, `SameSite=Lax` | Verificado en E2E |
| `scripts/supabase-live-check.py` contra el emisor simulado | **13 passed, 0 failed** (valida la herramienta, no el proyecto) |
| Backend Python 3.11 y 3.12 (Compose) | 93/93 y 93/93 (sin cambios de backend en esta fase) |

Revisión frente a `@supabase/ssr` y TanStack Start:
- `@supabase/ssr` escribe cookies con `httpOnly: false` y sin `Secure` por defecto. Se corrigió en el servidor: `HttpOnly`, `Secure` (desactivable solo con `SESSION_COOKIE_SECURE=false` en HTTP sin loopback), `SameSite=Lax`.
- CSRF: el middleware de TanStack rechaza con 403 las peticiones sin `Sec-Fetch-Site`, `Origin` ni `Referer` coincidentes; cubre login, logout y análisis.
- Renovación: `getClaims()` valida la firma y renueva; las cookies se reescriben en la misma respuesta. `getSession()` solo se usa después de que `getClaims()` valide la sesión, y FastAPI verifica el token de nuevo.
- Caché: todas las respuestas del servidor llevan `Cache-Control: no-store`.

Limitación abierta: FastAPI verifica tokens sin estado. Tras cerrar sesión en Supabase, el token sigue aceptado en FastAPI hasta su expiración. Decisión pendiente en `docs/PHASE2_AUTH_LIVE.md`, sección 7.

Pendiente para declarar la integración real verificada: credenciales del proyecto dedicado, usuario analista de prueba, ejecución de `scripts/supabase-live-check.py` con resultado real, y decisión sobre la revocación de tokens.
