# Fase 2.2: autenticación empresarial y autorización

Identidad: **Supabase Auth** (proveedor gestionado). Autoridad de permisos y de acceso a datos: **FastAPI con PostgreSQL**. El navegador no recibe tokens ni claves privilegiadas.

## Arquitectura

```
Navegador ──(cookies HttpOnly)──► TanStack Start (servidor) ──(Bearer, red privada)──► FastAPI ──► PostgreSQL
                                        │                                                 │
                                        └──► Supabase Auth (login, refresh, JWKS)          └── app_users, organization_memberships
```

- **Navegador:** formulario de login y botones. No contiene cliente de Supabase ni tokens. Toda acción de autenticación es una server function.
- **Servidor TanStack:** es el único componente que habla con Supabase Auth. Lee y escribe la sesión en cookies `HttpOnly`, valida la sesión con `getClaims()`, y reenvía el token a FastAPI como `Authorization: Bearer`.
- **FastAPI:** verifica criptográficamente cada token contra el JWKS del proyecto, resuelve el usuario interno, y decide organizaciones y roles en PostgreSQL. Nunca confía en un identificador de usuario enviado por el navegador.

Decisión registrada: el cliente de Supabase vive solo en el servidor. La documentación oficial de Supabase para TanStack Start también prevé un cliente de navegador. No lo usamos porque obligaría a exponer tokens al JavaScript de la página. La clave publicable se lee en tiempo de ejecución en el servidor (`SUPABASE_PUBLISHABLE_KEY`), no con prefijo `VITE_`, para que no quede incrustada en el bundle del navegador.

## Flujo de sesión

1. **Inicio de sesión:** `signIn` (server function) → `signInWithPassword` → Supabase devuelve tokens → `@supabase/ssr` los escribe en cookies `HttpOnly`. Errores 4xx producen `INVALID_CREDENTIALS`; cualquier otro fallo produce `AUTH_UNAVAILABLE`, sin detalles.
2. **Ruta protegida:** la ruta `/` tiene `beforeLoad` que llama a `getAccount` (server function). Sin sesión válida redirige a `/login`. Además, cada server function que consume recursos vuelve a verificar la sesión: la guarda de ruta no es la única barrera.
3. **Renovación:** `getClaims()` verifica la firma del token y lo renueva cuando está próximo a expirar; las cookies se actualizan en la misma respuesta.
4. **Sesión expirada o revocada:** si la renovación falla, la sesión se considera inexistente. El análisis responde `SESSION_EXPIRED`, la interfaz redirige a `/login?expired=1`, y no se realiza ninguna llamada al proveedor de IA.
5. **Cierre de sesión:** `signOut` (server function) revoca la sesión local en Supabase y limpia las cookies. La interfaz vuelve a `/login`.

## Verificación JWT en FastAPI

Implementada en `backend/app/security/tokens.py` con **PyJWT 2.15.1** (`pyjwt[crypto]`).

| Control | Implementación |
|---|---|
| Firma digital | Clave pública obtenida de `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` mediante `PyJWKClient` con caché de 10 minutos. |
| Algoritmos permitidos | Solo `ES256` y `RS256`, fijados en el código. El algoritmo nunca se toma del token. HS256 y `none` se rechazan. |
| Rotación de claves | La clave se busca por `kid`. Un `kid` nuevo fuerza la recarga del JWKS. |
| Issuer | `{SUPABASE_URL}/auth/v1` por defecto; configurable con `SUPABASE_JWT_ISSUER`. |
| Audience | `authenticated` por defecto; configurable con `SUPABASE_JWT_AUDIENCE`. |
| Expiración | `exp` obligatorio, con tolerancia de 10 segundos. |
| Subject | `sub` obligatorio y no vacío. Se vincula con `app_users.external_subject`. |
| Rol del token | `role` debe ser `authenticated`. |
| Tokens ajenos | Un token de otro proyecto falla por issuer. |

**Fallos de configuración:** sin `SUPABASE_URL`, las rutas autenticadas responden **503** (fail closed). No existe modo de "autenticación desactivada". Si el JWKS no responde, la respuesta es **503**, nunca 200. Un `kid` desconocido es un token inválido (**401**): un atacante no puede convertirlo en error de servicio.

**Sin secreto compartido:** no hay `SUPABASE_JWT_SECRET` ni rama HS256 en el código. La arquitectura objetivo son claves asimétricas.

## Usuarios internos y aprovisionamiento

- Un inicio de sesión exitoso crea solo una fila en `app_users` (identidad local). **No concede ninguna organización ni rol.**
- Las organizaciones y las membresías solo se crean con la CLI de operador, ejecutada en el servidor: `python -m app.cli create-organization`, `provision-user`, `grant-membership`. No hay endpoint HTTP que conceda `owner` ni `analyst`.
- `grant-membership` rechaza membresías duplicadas y usuarios o organizaciones inexistentes.

## Autorización por organización

| Caso | Respuesta |
|---|---|
| Sin token | 401 `AUTHENTICATION_REQUIRED` (con `WWW-Authenticate: Bearer`) |
| Token inválido, expirado, mal firmado, otro issuer o audience | 401 `INVALID_TOKEN` |
| Token válido, usuario sin membresía en la organización | 404 `NOT_FOUND` (no se revela que la organización existe) |
| Membresía `client_viewer` | 403 `FORBIDDEN`. **No se llama al proveedor de IA.** |
| Membresía `owner` o `analyst` | Análisis permitido |
| Organización suspendida | 403 `FORBIDDEN` |
| Identificador de organización inválido | 422 |

El análisis vive en `POST /api/v1/organizations/{organization_id}/analyses`. La autorización se valida **antes** de cualquier llamada al proveedor, así que una solicitud denegada nunca cuesta tokens. La ruta anterior `POST /api/v1/campaigns/analyze` fue eliminada, y la de organización `…/campaigns/analyze` está retirada desde la fase 2.6A (410 `ENDPOINT_RETIRED`): no queda ningún camino que consuma OpenRouter sin contar.

`GET /api/v1/me` devuelve solo `user.id`, `user.email` y las organizaciones del usuario con su rol. El `sub` del proveedor y otros campos internos no se exponen.

## Variables de entorno

| Variable | Dónde | Descripción |
|---|---|---|
| `SUPABASE_URL` | Backend y frontend (servidor) | URL del proyecto. En producción debe ser `https`. |
| `SUPABASE_PUBLISHABLE_KEY` | Frontend (servidor) | Clave publicable. Nunca una clave `service_role` ni un secreto. |
| `SUPABASE_JWT_ISSUER` | Backend (opcional) | Por defecto `{SUPABASE_URL}/auth/v1`. |
| `SUPABASE_JWT_AUDIENCE` | Backend (opcional) | Por defecto `authenticated`. |
| `FASTAPI_INTERNAL_URL` | Frontend | Ya existente. |
| `E2E_DATABASE_URL` | Solo pruebas E2E | Base cuyo nombre termina en `_test`. Se recrea en cada ejecución. |

Ninguna de estas variables aparece en el bundle del navegador: el frontend las lee solo en el servidor.

## Preparación del proyecto Supabase (pendiente de configuración real)

No se crearon proyecto, URL ni credenciales. Para activar el login real:

1. Crear un proyecto en Supabase para el piloto (región y plan a definir por RCKT).
2. En **Authentication → Sign In / Providers**, mantener solo **Email** y desactivar el registro público (**Allow new users to sign up** desactivado). Las cuentas las crea RCKT.
3. En **Authentication → Signing Keys**, usar claves asimétricas (ES256 o RS256). No usar el JWT secret compartido.
4. Copiar la URL del proyecto a `SUPABASE_URL` y la clave publicable a `SUPABASE_PUBLISHABLE_KEY` en el `.env` del servidor.
5. Crear las cuentas de usuarios, que aparecerán en `app_users` tras su primer inicio de sesión, y asignarles membresías con la CLI.

Hasta que se completen estos pasos, el sistema falla cerrado: el login muestra un error y la aplicación no se abre.

## Pruebas

**Backend (sin proveedor real):**
- `tests/test_tokens.py`: 19 pruebas de verificación criptográfica con claves ficticias, incluido un JWKS HTTP real (`PyJWKClient`) y un JWKS caído.
- `tests/test_authz_api.py`: autenticación y autorización en HTTP contra PostgreSQL: tokens ausentes, expirados, falsificados, de otro proyecto, membresías, roles, organizaciones cruzadas, suspensión, fail closed y ausencia de eco de tokens.
- `tests/test_cli.py`: procedimientos de aprovisionamiento.
- `tests/test_api.py`: contrato del análisis con autenticación.

**E2E (Playwright, sin Supabase real y sin OpenRouter real):**
- `e2e/fake-supabase.py`: emisor simulado con claves ES256 ficticias. Publica JWKS y emite tokens firmados de verdad; no es una puerta trasera de la aplicación y solo lo inicia el webServer de pruebas.
- `e2e/prepare_database.py`: recrea `rckt_e2e_test` y siembra identidades **con la CLI de operador**.
- `e2e/auth.spec.ts` y `e2e/campaign.spec.ts`: escenarios de sesión, roles, sesión expirada y ausencia de credenciales en el navegador.

Los resultados exactos están en `docs/PHASE2_AUTH_RESULTS.md`.

## Limitaciones y riesgos

- **No se probó contra un proyecto Supabase real.** La integración se verificó con un emisor simulado que usa el mismo formato de tokens, JWKS y endpoints. Hay que validarla con el proyecto real antes de producción.
- **Sin registro de usuarios:** es intencional. Las cuentas las crea RCKT manualmente.
- **Sin SSO corporativo.** Queda para la fase 2.8.
- **Sin límite de intentos de login propio.** Depende del proveedor; hay que confirmar sus límites en el plan elegido.
- **Sin revocación inmediata de tokens de acceso ya emitidos.** Un token válido sigue aceptado hasta su expiración (por defecto una hora). Mitigación: vida corta del token y revocación de sesiones en el proveedor.
- **La selección de organización es la primera membresía por nombre.** Un selector explícito en la interfaz queda para una iteración posterior; el backend ya acepta `organizationId`.
- **Sin RLS en PostgreSQL** (etapa 2.7).
- **Tests de Compose:** el stack completo con Supabase real requiere credenciales reales; no se probó en este entorno.
