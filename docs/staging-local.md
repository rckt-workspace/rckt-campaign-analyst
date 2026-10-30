# Staging local: PostgreSQL administrada por RCKT

Estado: **preparado y verificado localmente** en una instancia PostgreSQL 16 desechable. No desplegado. No aplicado a ninguna
base real, a Lovable Cloud ni a Supabase Cloud. Este documento distingue lo verificado de lo pendiente.

## 1. Decisión de arquitectura

Lovable Cloud no admite conexiones PostgreSQL externas directas (documentación oficial de Supabase, según el diagnóstico
del responsable). Por eso la base empresarial de FastAPI no será Lovable Cloud. Lovable queda como herramienta de frontend.

La base del backend será una PostgreSQL controlada por RCKT. Su proveedor, región y coste **requieren aprobación**.

Estado del esquema en Lovable Cloud, según el diagnóstico: fundación y campañas presentes; migraciones 0004 a 0009 ausentes.
Ninguna de ellas se ejecutará en Lovable Cloud.

## 2. Verificado localmente

Ejecución del verificador `scripts/security_poc/run_rls_suite.py` sobre una instancia desechable etiquetada
(`rckt.purpose=db-security-2.7b2`, PostgreSQL 16.15, publicada solo en `127.0.0.1`), con las guardas de identidad del contenedor.

| Comprobación | Resultado |
|---|---|
| Roles aprovisionados con `scripts/db_roles/provision_roles.sql` | Correcto |
| `alembic upgrade head` desde base vacía (0001 a 0009) | Correcto |
| Esquema de Alembic frente al SQL portable 0001 a 0009 | Idénticos, 670 líneas normalizadas |
| `downgrade` a 0008 frente al SQL portable 0001 a 0008 | Idénticos, 463 líneas normalizadas |
| Efecto de los privilegios por defecto, en ambos sentidos | Correcto |
| Ninguna función de `public` ejecutable por `PUBLIC` | Correcto |
| Suite completa del backend (incluye RLS, ciclo de vida de identidad, integración con `rckt_app` y guardia de plantilla) | **515 aprobadas, 0 fallos** |

Frontend, en la misma rama:

| Comprobación | Resultado |
|---|---|
| `node scripts/run-unit-tests.mjs` | 52 aprobadas, 0 fallos |
| `tsc --noEmit` | Sin errores |
| `node scripts/build-node.mjs` | Correcto |

Limitación de la suite: de las 515, las de negocio (451) siguen ejecutándose con el rol propietario equivalente. Las 62 de
seguridad, identidad e integración usan `rckt_app`. Ver `docs/PHASE2_DB_RLS.md` §13.2.

## 3. Pendiente (no verificado)

- **E2E de navegador (Playwright).** No ejecutado: al momento de la prueba había 546 MB libres y el puerto 3100 estaba ocupado
  por el contenedor del frontend, que la prueba necesita liberar. En sesiones anteriores, un E2E completo fue terminado por
  falta de memoria.
- **Inicio de sesión real** con el proveedor de autenticación. Ninguna prueba de login contra un proyecto real.
- **Staging administrado real.** Nada se ha creado ni contratado.
- **Migraciones en Lovable Cloud o Supabase.** No ejecutadas, por decisión.

## 4. Crear la base de staging (procedimiento)

Requiere una instancia PostgreSQL 16 nueva y dedicada, y un rol con permiso `CREATE ROLE` solo para esta operación. Ningún paso
se ejecuta contra una base existente.

1. **Crear la base vacía** con un nombre sin `_test`, por ejemplo `rckt_staging`, propiedad de `rckt_migrator`.
   Ese rol debe existir antes.
2. **Aprovisionar los roles** con `scripts/db_roles/provision_roles.sql`, ejecutado por el rol que puede crear roles. El script
   aborta si un rol existente tiene `SUPERUSER` o `BYPASSRLS`, no define contraseñas y no altera roles existentes.
3. **Definir las contraseñas** de `rckt_app`, `rckt_migrator` y de cada operador **fuera del repositorio**, por la persona
   responsable. Nunca en un archivo versionado ni en la imagen.
4. **Aplicar las migraciones** como `rckt_migrator`: `DATABASE_MIGRATION_URL=... alembic upgrade head` desde `backend/`.
   Las migraciones 0001 a 0009 no crean roles ni contienen credenciales.
5. **Verificar catálogo** con las consultas de solo lectura de `docs/PHASE2_DB_SECURITY.md` §10.3. **No usar
   `scripts/db_security_audit.py` sobre staging**: exige que el nombre de la base termine en `_test` (decisión de seguridad).
6. **Crear un login por operador** (`rckt_operator_<persona>`, miembro de `rckt_operator`). Nunca compartir un login.
7. **Configurar FastAPI** con `DATABASE_URL` apuntando a `rckt_app`.

Rollback: `alembic downgrade 0008_audit_events` como `rckt_migrator`. No borra roles ni datos de fundación/campañas. Ver
`docs/PHASE2_DB_RLS.md` §10.

## 5. Variables por componente

Nombres y propósito. Los valores reales nunca se versionan. La plantilla está en `.env.example`, y el test
`backend/tests/test_env_template.py` falla si el backend lee una variable que no esté documentada allí.

| Componente | Variable | Obligatoria | Propósito | Secreto |
|---|---|---|---|---|
| FastAPI (runtime) | `DATABASE_URL` | Sí | Login `rckt_app` | Sí |
| FastAPI | `SUPABASE_URL` | Sí (para rutas con sesión) | Emisor y JWKS del proveedor de autenticación | No (URL) |
| FastAPI | `SUPABASE_JWT_ISSUER`, `SUPABASE_JWT_AUDIENCE` | No | Por defecto `<SUPABASE_URL>/auth/v1` y `authenticated` | No |
| FastAPI | `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `CHAT_*` | Sí para análisis reales | Proveedor de IA | Sí (la clave) |
| FastAPI | `ANALYSIS_QUOTA_ENFORCED` | No (por defecto `false`) | Activa cuotas | No |
| FastAPI | `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_CONNECT_TIMEOUT_SECONDS` | No | Pool | No |
| Migraciones | `DATABASE_MIGRATION_URL` | Solo en el trabajo de migración | Login `rckt_migrator` | Sí |
| Operador (CLI) | `DATABASE_OPERATOR_URL` | Solo para la CLI | Login personal de operador | Sí |
| Frontend (servidor) | `FASTAPI_INTERNAL_URL` | Sí | URL interna de FastAPI (por defecto `http://127.0.0.1:8000`) | No |
| Frontend (servidor) | `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | Sí | Autenticación desde el servidor | La clave publicable no es secreta, pero no se expone al navegador |

**Nunca** se usa `SUPABASE_SERVICE_ROLE_KEY` como sustituto del rol de FastAPI. Ver §7.

## 6. Autenticación

Opción A (recomendada si la integración se valida): los tokens del proveedor se verifican en FastAPI con claves JWKS,
aceptando solo `ES256` y `RS256`. El algoritmo lo fija el módulo, no el token. Implementado y probado con tokens de prueba.

**No verificado:** un inicio de sesión real, la sincronización de usuarios entre el proveedor y `app_users`, y que el
proveedor usado sea el que expone Lovable. Hasta probarlo, no se afirma que funcione.

Opción B (alternativa propia): FastAPI emite sus propios tokens ES256 con claves gestionadas por un servicio de secretos.
Requiere implementar registro, recuperación de contraseña, rotación de claves y MFA. Mayor coste; sin dependencia del
proveedor.

## 7. Riesgos y pendientes

1. **`src/integrations/supabase/client.server.ts`** (generado por Lovable) lee `SUPABASE_SERVICE_ROLE_KEY` y salta RLS.
   Hoy no se importa en ningún sitio: solo aparece citado en un comentario. Un test lo vigila. Eliminarlo o protegerlo es
   una decisión que requiere aprobación, porque es código generado por la plataforma.
2. **Auditoría R13:** `create-organization` y `provision-user` no generan evento de auditoría.
3. **Incidencia abierta:** en una ejecución sobre una base recién creada falló una vez una prueba de cuota por CLI. No se
   reprodujo en cinco ejecuciones posteriores. Ver `docs/PHASE2_DB_RLS.md` §13.11.
4. **Rendimiento de RLS:** no medido con volumen real.
5. **Migrador con privilegios elevados:** hereda la propiedad del esquema. Aceptado mientras no atienda peticiones.
6. **Privilegios de Lovable Cloud y Supabase:** no verificados.

## 8. Qué desplegar y dónde (decisión pendiente)

Componentes:

- **Backend FastAPI:** `backend/Dockerfile`.
- **Frontend TanStack Start:** `Dockerfile` raíz, o el servidor Node generado por `build:node` (`node .output/server/index.mjs`).
- **PostgreSQL 16 gestionada por RCKT.**

No existe `render.yaml` en este repositorio (verificado). Desplegar en Render, en otra plataforma o en AWS **requiere
aprobación**. Ningún despliegue se ha realizado.

## 9. Acciones que requieren aprobación de la empresa

1. Elegir y contratar la PostgreSQL de staging (proveedor, región, coste).
2. Crear o designar el proyecto de autenticación y sus usuarios de prueba.
3. Crear los logins personales de operador.
4. Decidir la plataforma de despliegue.
5. Decidir sobre `client.server.ts`.
6. Autorizar aplicar las migraciones en el staging elegido.
7. Autorizar la publicación de la rama y del PR.
