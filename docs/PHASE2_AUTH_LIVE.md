# Fase 2.2B: activar Supabase Auth real (proyecto de desarrollo dedicado)

Estado: **procedimiento preparado; no ejecutado todavía.** No existe un proyecto Supabase configurado para RCKT Campaign Analyst en este entorno, y no se usan credenciales de otros proyectos. Hasta que se completen estos pasos, la integración real **no está verificada**.

## 1. Crear un proyecto dedicado (no reutilizar)

En el panel de Supabase, dentro de la organización de RCKT:

1. Crear un proyecto nuevo, por ejemplo `rckt-campaign-analyst-dev`. **No usar proyectos existentes**, ni los de la instancia local de Supabase CLI de este equipo: pertenecen a otros proyectos.
2. Guardar la contraseña de la base del proyecto en un gestor de contraseñas. Este procedimiento no la necesita y nunca debe escribirse en el repositorio.
3. Esperar a que el proyecto esté listo.

## 2. Configurar Auth

1. **Authentication → Sign In / Providers → Email**: activar el proveedor de correo y contraseña.
2. **Authentication → Sign In / Providers → Email → Allow new users to sign up**: **desactivar**. Las cuentas las crea RCKT; nadie puede registrarse.
3. **Authentication → Sign In / Providers**: desactivar proveedores sociales que no se usen.
4. **Authentication → Rate limits / Settings**: revisar los límites de intentos de inicio de sesión del plan.
5. **Project Settings → JWT Keys / Signing keys**: confirmar que se usan **claves de firma asimétricas (ES256)**. No usar el JWT secret compartido (HS256) en esta aplicación.

## 3. Crear el usuario de prueba (analista)

1. **Authentication → Users → Add user → Create new user**: correo de prueba y contraseña fuerte, con confirmación automática.
2. Copiar el **UUID** del usuario (columna `id`). Es el `sub` del token y se usa en el paso 5.
3. No crear más usuarios hasta completar la verificación.

## 4. Variables locales

Solo en el `.env` del servidor o del equipo que ejecuta la verificación. **Nunca en el repositorio.**

```
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<clave publicable del proyecto (Project Settings → API)>
```

- La clave publicable puede estar en el frontend, pero esta aplicación la usa solo en el servidor.
- **Nunca** usar la clave `service_role` ni un secreto de firma. La aplicación no las necesita y no debe tenerlas.

## 5. Aprovisionamiento seguro (solo con la CLI de operador)

Desde `backend/`, con `DATABASE_URL` apuntando a la base de la aplicación (PostgreSQL de desarrollo). Ninguno de estos comandos es un endpoint HTTP.

```bash
python -m app.cli create-organization --name "Cliente de prueba" --slug cliente-prueba
python -m app.cli provision-user --subject <UUID-del-usuario> --email <correo-de-prueba>
python -m app.cli grant-membership --organization-slug cliente-prueba --role analyst --subject <UUID-del-usuario>
```

- `provision-user` crea solo la identidad local. No concede acceso.
- `grant-membership` concede el rol `analyst` de forma explícita. Repetirlo falla a propósito si la membresía ya existe.
- Para un usuario de solo lectura, repetir el paso con otro usuario y `--role client_viewer`. Sirve para comprobar la restricción de IA.

## 6. Verificación

### 6.1 Pruebas automáticas (sin credenciales reales)

```bash
cd backend && python -m pytest -q tests        # identidad simulada, PostgreSQL de pruebas
cd .. && bun run test:e2e                      # emisor JWT simulado, proveedor de IA simulado
```

### 6.2 Comprobación en vivo (opt-in)

`scripts/supabase-live-check.py` valida el proyecto real. Lee las credenciales del entorno del proceso, no de archivos, y solo imprime PASS o FAIL. Nunca imprime tokens ni claves.

Antes, levantar FastAPI apuntando al proyecto real. Para el paso de análisis, apuntar a un **proveedor simulado**, de modo que no haya llamadas de pago:

```bash
# Terminal 1: proveedor simulado (no consume créditos)
python e2e/fake-openrouter.py                   # escucha en 127.0.0.1:18080

# Terminal 2: FastAPI con el proyecto real y el proveedor simulado
cd backend
SUPABASE_URL=https://<project-ref>.supabase.co \
DATABASE_URL=postgresql+asyncpg://... \
OPENROUTER_API_KEY=placeholder-not-used-by-simulator \
OPENROUTER_BASE_URL=http://127.0.0.1:18080/api/v1 \
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 3: comprobación (credenciales solo en el entorno de esta sesión)
export SUPABASE_URL=https://<project-ref>.supabase.co
export SUPABASE_PUBLISHABLE_KEY=<clave publicable>
export LIVE_TEST_EMAIL=<correo-de-prueba>
export LIVE_TEST_PASSWORD=<contraseña-de-prueba>
export LIVE_ORGANIZATION_SLUG=cliente-prueba
export LIVE_OPENROUTER_SIMULATED=1
# Opcional, para comprobar la restricción de client_viewer:
export LIVE_VIEWER_EMAIL=<correo-viewer> LIVE_VIEWER_PASSWORD=<contraseña-viewer>
python scripts/supabase-live-check.py
```

Qué comprueba:
1. JWKS publica claves asimétricas (ES256 o RS256).
2. Inicio de sesión real con correo y contraseña.
3. `GET /api/v1/me` acepta el token real y muestra la membresía `analyst`.
4. Sin token y con token alterado: 401.
5. Organización sin membresía: 404.
6. Análisis con el proveedor simulado: 200 (requiere `LIVE_OPENROUTER_SIMULATED=1`).
7. `client_viewer`: 403 para análisis (si se configura).
8. Renovación de sesión con refresh token.
9. Cierre de sesión en el proveedor y rechazo del token en el proveedor.

**Nunca** ejecutar el paso de análisis contra OpenRouter real. La variable `LIVE_OPENROUTER_SIMULATED=1` es una confirmación explícita de que el proveedor es el simulado; el script no puede comprobarlo por sí mismo.

### 6.3 Verificar el aislamiento entre organizaciones

Crear una segunda organización (`cliente-otro`) sin membresía para el analista. Con el token del analista, el paso 5 debe devolver 404 para esa organización. Hoy el script lo comprueba con un UUID inexistente; la comprobación real con una organización existente y ajena se añade cuando exista un segundo cliente.

## 7. Cierre de sesión y limitación conocida

Tras cerrar sesión en Supabase, FastAPI sigue aceptando el token **hasta su expiración** (por defecto una hora), porque verifica firmas sin consultar estado. Mitigaciones, por orden de preferencia:

1. Reducir la vida del access token en el proyecto de Supabase (**JWT expiry**) para el piloto.
2. Consultar `/auth/v1/user` desde FastAPI en las operaciones que consumen IA (añade latencia y dependencia del proveedor).
3. Lista de revocación de `sub` en PostgreSQL, consultada en cada petición.

Decisión pendiente. Hasta resolverla, la verificación en vivo lo muestra como nota y no como fallo.

## 8. Qué falta para declarar la integración real verificada

- Credenciales del proyecto dedicado (URL y clave publicable), entregadas por el responsable de RCKT.
- Usuario analista y, opcionalmente, usuario `client_viewer` creados en ese proyecto.
- Ejecución de `scripts/supabase-live-check.py` con resultado 13/13 (o el número de pasos aplicables) y pegado en `docs/PHASE2_AUTH_RESULTS.md`.
- Decisión sobre la revocación de tokens (sección 7).
