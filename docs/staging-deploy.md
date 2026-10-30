# Staging: procedimiento listo para ejecutar cuando se autorice

Estado: **preparado, no desplegado.** Este documento cubre lo que hay que ejecutar cuando la empresa autorice la
infraestructura (PostgreSQL gestionada y plataforma de despliegue) y la autenticación (Lovable Auth). No crea recursos, no
contrata servicios y no llama a OpenRouter. Base de partida: `docs/staging-local.md`.

Lo que **no** está verificado todavía, con su evidencia:

- Inicio de sesión real con Lovable Auth: no probado. Ver §5.
- Pruebas E2E de navegador: no ejecutadas en este equipo (memoria insuficiente). Ver §7.
- Auditoría de operaciones administrativas (R13): `create-organization` y `provision-user` no generan evento. Pendiente.
- Incidencia intermitente de cuota por CLI: una vez en una ejecución sobre base nueva. Ver `docs/PHASE2_DB_RLS.md` §13.11.

## 1. Decisiones ya tomadas

- FastAPI + PostgreSQL 16 controlada por RCKT. Lovable Cloud no admite conexiones PostgreSQL externas, así que no es la base.
- Lovable actúa como frontend y como proveedor de autenticación (si se valida, §5).
- Las migraciones 0001 a 0009 no cambian. Las correcciones irán en migraciones nuevas.
- Cuatro identidades de base de datos, cada una para un único propósito. Ninguna se reutiliza:

| Identidad | Dónde se usa | Puede |
|---|---|---|
| `rckt_app` | Runtime de FastAPI (`DATABASE_URL`) | Leer y escribir con RLS. Sin SUPERUSER, sin BYPASSRLS, sin ser propietaria |
| `rckt_migrator` | Solo `alembic upgrade/downgrade` (`DATABASE_MIGRATION_URL`) | Actuar como propietaria del esquema. Nunca en el runtime |
| `rckt_operator_<persona>` | Solo CLI (`DATABASE_OPERATOR_URL`), un login por persona | Ejecutar cuatro funciones `SECURITY DEFINER` |
| Administrador del proveedor | Solo aprovisionamiento inicial | Crear roles y la base. Nunca en el runtime |

## 2. Variables de entorno

Nombres y propósito. Los valores reales se introducen en la plataforma o en el gestor de secretos, nunca en el repositorio.
La plantilla completa es `.env.example`, y `backend/tests/test_env_template.py` vigila que el backend no lea nada sin documentar.

| Servicio | Variable | Valor / origen | Secreto |
|---|---|---|---|
| Frontend (público) | `FASTAPI_INTERNAL_URL` | `http://rckt-campaign-api:8000` (nombre del servicio privado; introducir a mano) | No |
| Frontend | `SUPABASE_URL` | URL del proyecto de autenticación | No |
| Frontend | `SUPABASE_PUBLISHABLE_KEY` | Clave **publicable** (nunca de servicio) | No (no se expone al navegador) |
| FastAPI (privado) | `DATABASE_URL` | `postgresql+asyncpg://rckt_app:…@<host>:5432/rckt_staging` | **Sí** |
| FastAPI | `SUPABASE_URL` | Igual que el frontend | No |
| FastAPI | `SUPABASE_JWT_ISSUER` | Por defecto `<SUPABASE_URL>/auth/v1`; solo definir si difiere | No |
| FastAPI | `SUPABASE_JWT_AUDIENCE` | `authenticated` | No |
| FastAPI | `OPENROUTER_API_KEY` | Solo cuando el propietario apruebe gasto | **Sí** |
| FastAPI | `OPENROUTER_BASE_URL`, `CHAT_*` | Valores de `render.yaml` | No |
| FastAPI | `ANALYSIS_QUOTA_ENFORCED` | `false` hasta decisión del propietario | No |
| Migraciones (una vez) | `DATABASE_MIGRATION_URL` | `rckt_migrator` | **Sí** |
| Operador (una vez por persona) | `DATABASE_OPERATOR_URL` | Login personal de `rckt_operator` | **Sí** |

**Nunca** `SUPABASE_SERVICE_ROLE_KEY` en ningún servicio. `src/integrations/supabase/client.server.ts` la lee, pero no se
importa (vigilado por test). Eliminarlo o protegerlo requiere aprobación.

## 3. Servicios y plataforma (propuesta)

`render.yaml` describe: frontend público (`web`, `Dockerfile` raíz), API privada (`pserv`, `backend/Dockerfile`) y
PostgreSQL 16 gestionada. Es una **propuesta sin aplicar**. Antes de aplicarla hay que comprobar en la especificación oficial
(https://render.com/docs/yaml-spec) estos puntos, que no se pudieron verificar de forma concluyente:

- Que `pserv` tenga el nombre de servicio como nombre de host privado, y el puerto 8000 expuesto por el contenedor.
- Que el usuario que crea Render para la base pueda ejecutar `CREATE ROLE`. Si no puede, `provision_roles.sql` no se ejecuta
  allí y hay que elegir otro proveedor de PostgreSQL.
- Que `ipAllowList: []` deje la base accesible solo desde la red privada.
- Que los planes indicados como de pago tengan el coste aceptado. Ningún plan se ha contratado.

Región: `frankfurt` como propuesta. Debe coincidir entre base y servicios. Decisión pendiente.

## 4. Orden de la primera puesta en marcha

Cada paso lo ejecuta una persona autorizada y se detiene si el anterior falla.

1. **Aprobar** la plataforma, la región y el coste (§9).
2. **Aplicar el blueprint** `render.yaml` desde el panel. Los secretos (`sync: false`) quedan vacíos hasta el paso 5.
3. **Aprovisionar roles** una vez, con el administrador de la base, fuera del runtime:
   `psql "<URL administrador>" -v ON_ERROR_STOP=1 -f scripts/db_roles/provision_roles.sql`.
   El script aborta si un rol existente tiene SUPERUSER o BYPASSRLS. No define contraseñas.
4. **Definir contraseñas** de `rckt_app` y `rckt_migrator` en el gestor de secretos, y crear los logins personales de operador
   (`CREATE ROLE rckt_operator_<persona> LOGIN NOSUPERUSER NOBYPASSRLS INHERIT; GRANT rckt_operator TO rckt_operator_<persona>;`).
   Las contraseñas no se escriben en ningún archivo.
5. **Migrar** como `rckt_migrator`, desde `backend/`:
   `DATABASE_MIGRATION_URL="<URL migrator>" python -m alembic upgrade head`.
   Después: `python -m alembic current` debe mostrar `0009`.
6. **Verificar catálogo** (ver §8, prueba 1). No usar `scripts/db_security_audit.py` sobre staging: exige un nombre `_test`.
7. **Configurar** `DATABASE_URL` (rckt_app) y los demás secretos del servicio privado. Desplegar la API.
8. **Configurar** `FASTAPI_INTERNAL_URL` en el frontend y desplegarlo.
9. **Crear datos de prueba** (§6) y ejecutar las pruebas de aceptación (§8).

## 5. Lovable Auth: requisitos y verificación

Requisitos a comprobar **en el proyecto de autenticación** (nunca cambiarlos sin aprobación):

1. **Inicio de sesión por correo** activo.
2. **Claves de firma JWT:** la clave que firma los tokens debe estar **activa** (en uso). Una clave visible en JWKS no
   demuestra que firme; puede estar en espera o rotada. La comprobación de verdad es la del paso 4.
3. **Emisor:** `<SUPABASE_URL>/auth/v1`. **Audiencia:** `authenticated`.
4. **JWKS:** `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`. FastAPI acepta solo `ES256` y `RS256`, y fija el algoritmo en el módulo.

Procedimiento de verificación real (solo en un proyecto de desarrollo dedicado, nunca en producción):

1. Crear un usuario de prueba en ese proyecto. Su contraseña se introduce en el shell, no en un archivo.
2. Ejecutar `scripts/supabase-live-check.py`, siguiendo `docs/PHASE2_AUTH_LIVE.md`. Solo muestra PASS/FAIL, sin tokens ni claves.
3. Comprobar que el `kid` del token coincide con una clave **activa** de JWKS, y que `alg` es `ES256` o `RS256`.
4. Si el `kid` no coincide, FastAPI rechazará los tokens. Hay que corregir la clave activa antes de seguir.

Estado: **no verificado** en ningún proyecto real. No se afirma que el proveedor que expone Lovable cumpla estos puntos.

## 6. Datos de prueba: organización, dos usuarios y campañas

Sin datos de clientes reales. Usar nombres ficticios y filas sintéticas. Ningún secreto en el repositorio.

Modelo: `e2e/identities.json` (organizaciones `cliente-a`/`cliente-b`, usuarios con roles `analyst`, `owner`, `client_viewer`).
Para staging se crean nombres nuevos, por ejemplo `cliente-prueba-a`.

1. **Crear los dos usuarios** en el proyecto de autenticación (correo ficticio, contraseña generada y guardada en el gestor).
   Anotar su `sub` (identificador de usuario).
2. **Crear la organización** con el login personal de operador:
   `python -m app.cli create-organization --name "Cliente de prueba A" --slug cliente-prueba-a`.
3. **Registrar cada usuario:**
   `python -m app.cli provision-user --subject <sub> --email <correo ficticio>`.
4. **Asignar rol:** `python -m app.cli grant-membership --organization-slug cliente-prueba-a --role analyst --subject <sub>`.
   Usar `analyst` para uno y `client_viewer` para el otro. Ambos en la misma organización.
5. **Crear campañas de ejemplo** entrando como el analista en la aplicación, con filas sintéticas.

Nota: `create-organization` y `provision-user` no generan evento de auditoría (R13). Hasta resolverlo, dejar constancia
en la hoja de operaciones del operador.

## 7. Pruebas E2E de navegador

Qué hace: compila el frontend en modo Node, prepara una base `rckt_e2e_test` (la borra y la recrea), y levanta un
proveedor OpenRouter simulado, un emisor de autenticación simulado, FastAPI y el servidor Node en los puertos 18080, 18000
y 3100. Playwright usa Chromium. No llama a OpenRouter, no consume créditos y no usa Lovable Auth.

Variables (solo `_test`; nunca la base de desarrollo, ni la de staging):

| Variable | Login | Uso |
|---|---|---|
| `E2E_ADMIN_DATABASE_URL` | Administrador local | Borrar y crear la base de prueba |
| `E2E_MIGRATION_DATABASE_URL` | `rckt_migrator` | Migraciones |
| `E2E_OPERATOR_DATABASE_URL` | Miembro de `rckt_operator` | Sembrar datos con la CLI |
| `E2E_APP_DATABASE_URL` | `rckt_app` | El backend bajo prueba |

Ejecución (desde la raíz, con el archivo `.env` o las variables definidas en el entorno):

```
bun install --frozen-lockfile
pip install -r backend/requirements-dev.txt
bunx playwright install chromium
bun run test:e2e
```

Requisitos y límites en este equipo:

- **Memoria:** en la última comprobación había unos 546 MB libres. Una ejecución completa fue terminada por falta de memoria
  en una sesión anterior. Para ejecutarla aquí hay que cerrar cargas propias (no servicios ajenos) y tener varios GB libres.
- **Puerto 3100:** debe estar libre. No detener contenedores de otros proyectos para liberarlo.
- **CI:** el job `e2e` de `.github/workflows/docker-ci.yml` ya usa las cuatro identidades con un PostgreSQL desechable. Está
  **bloqueado por la facturación de GitHub Actions**: no es una prueba pasada.

Recomendación: ejecutar en un equipo con más memoria, o en CI cuando se resuelva la facturación. Los resultados se
registrarán en `docs/PHASE2_AUTH_RESULTS.md` con el comando y la salida.

## 8. Pruebas de aceptación

Se ejecutan contra staging tras el paso 9 de §4. Ninguna se da por superada sin evidencia.

1. **Roles (operador, con el administrador):** `rckt_app` y `rckt_migrator` existen, sin SUPERUSER ni BYPASSRLS.
   `SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname LIKE 'rckt%';`
2. **Migración:** `alembic current` = 0009.
3. **Salud:** la API responde `/healthz` con 200 (desde el panel o un servicio de la misma red).
4. **Inicio de sesión:** el analista entra en la aplicación. Su sesión se mantiene al recargar.
5. **Aislamiento entre organizaciones:** el analista de `cliente-prueba-a` no ve ni modifica datos de otra organización.
6. **Rol de solo lectura:** el `client_viewer` ve campañas y no puede crear análisis (403).
7. **Usuario sin pertenencia:** recibe 403 en cualquier organización.
8. **Token caducado o sin firma válida:** 401.
9. **Análisis:** **solo** si el propietario aprueba el gasto de OpenRouter. Sin aprobación, esta prueba queda pendiente.
   No se sustituye por una prueba simulada en staging.
10. **Ningún secreto visible:** el HTML y las respuestas del frontend no contienen claves ni URLs internas.

## 9. Rollback seguro

Principio: no se destruyen roles ni datos. Se vuelve a una versión conocida.

1. **Antes de cualquier cambio de esquema:** copia de seguridad de la base por el operador (`pg_dump`, o la copia
   automática de la plataforma si está contratada).
2. **Aplicación:** volver al despliegue anterior desde el panel de Render. Sin `autoDeploy`, el rollback también es manual.
3. **Migración:** `DATABASE_MIGRATION_URL=... python -m alembic downgrade 0008_audit_events` como `rckt_migrator`.
   No borra roles ni datos de fundación o campañas. Ver `docs/PHASE2_DB_RLS.md` §10. Es la única operación de esquema
   permitida en rollback.
4. **Roles:** no eliminar roles en un rollback. Si hay que retirar un login, `ALTER ROLE ... NOLOGIN` tras revisión.
5. **Verificación tras rollback:** repetir §8, pruebas 1 a 3.
6. **Registro:** anotar en la hoja de operaciones qué se revirtió, cuándo y con qué copia.

## 10. Pendiente antes de la primera puesta en marcha

1. Elegir y contratar la PostgreSQL (proveedor, región, coste). Confirmar que permite `CREATE ROLE`.
2. Confirmar la plataforma de despliegue (Render u otra). Sin aprobación no se aplica `render.yaml`.
3. Crear o designar el proyecto de autenticación, y verificar §5.
4. Crear los logins personales de operador.
5. Decidir sobre `client.server.ts` (`SUPABASE_SERVICE_ROLE_KEY`).
6. Resolver R13 (auditoría de `create-organization` y `provision-user`).
7. Investigar la incidencia de cuota por CLI, o documentar que no se reproduce.
8. Verificar privilegios de Lovable Cloud y Supabase (no verificados).

## 11. Autorizaciones necesarias

1. Plataforma, región y coste.
2. Proyecto de autenticación y usuarios de prueba.
3. Logins personales de operador.
4. Aplicar migraciones en el staging elegido.
5. Gasto de OpenRouter, si se quiere la prueba 9.
6. Publicar la rama o los PR. Este trabajo permanece en local.
