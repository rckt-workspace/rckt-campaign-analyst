# Staging: procedimiento para cuando se autorice la infraestructura y la autenticación

Estado: **preparado, no desplegado.** No crea recursos, no contrata servicios, no aplica migraciones y no llama a OpenRouter.
Base de partida: `docs/staging-local.md`. Cada afirmación de esta página se marca como **verificada** (con su fuente) o
**requiere prueba** (no comprobable desde el repositorio ni desde la documentación consultada).

## 1. Decisiones

- Plataforma propuesta: **Render**, región **Virginia** para frontend, API privada y PostgreSQL.
- PostgreSQL **16**, la versión validada localmente.
- **Ningún servicio de pago está autorizado.** Los planes de pago de `render.yaml` quedan sin aprobar (§12).
- FastAPI + PostgreSQL controlada por RCKT. Lovable Cloud no admite conexiones PostgreSQL externas, así que no es la base.
- Migraciones 0001 a 0009 sin cambios. Las correcciones irán en migraciones nuevas.

## 2. Identidades de base de datos

Cuatro logins, uno por propósito. Ninguno se reutiliza.

| Identidad | Dónde | Puede | Nunca |
|---|---|---|---|
| `rckt_app` | `DATABASE_URL` de FastAPI | Leer y escribir con RLS. Sin SUPERUSER, sin BYPASSRLS, sin ser propietaria | Migrar, administrar roles |
| `rckt_migrator` | `DATABASE_MIGRATION_URL`, solo migraciones | Actuar como propietaria del esquema por pertenencia | Atender peticiones |
| `rckt_operator_<persona>` | `DATABASE_OPERATOR_URL`, solo CLI | Ejecutar cuatro funciones `SECURITY DEFINER` | Acceso a tablas |
| Usuario de administración de Render | Aprovisionamiento inicial | Crear roles y ejecutar la migración (§4) | Ser `DATABASE_URL` |

El usuario que Render crea por defecto para la base **no** es `rckt_app`. Su nombre y privilegios no están documentados en lo
consultado (§3). Por eso `DATABASE_URL` se introduce a mano en el panel, con `rckt_app`.

## 3. Comprobación de `render.yaml` contra la especificación

Fuentes consultadas: [Blueprint YAML Reference](https://render.com/docs/yaml-spec) y
[PostgreSQL: creating and connecting](https://render.com/docs/postgresql-creating-connecting).

**Verificado en la documentación:**

- `fromService` con `property: hostport` existe para servicios `web` y `pserv`, y devuelve `host:puerto` (sin esquema).
  **Diferencia resuelta:** el frontend añade `http://` cuando falta el esquema (`normalizeFastapiBaseUrl` en
  `src/lib/fastapi-client.server.ts`, probada en `src/lib/fastapi-base-url.test.ts`). No se usa interpolación.
- Render **no soporta interpolación de variables** en un blueprint. Por eso la URL no se compone en el YAML.
- `sync: false` es válido: el valor se pide al crear el blueprint y no se guarda en el archivo.
- `healthCheckPath` solo aplica a servicios `web`. Se usa en el frontend, no en la API.
- `preDeployCommand` existe para servicios, pero **no se usa**: las migraciones no corren al desplegar.
- Regiones válidas: `oregon`, `ohio`, `virginia`, `frankfurt`, `singapore`. Se usa `virginia`.
- PostgreSQL: versiones 13 a 18 disponibles para instancias nuevas. Se usa `"16"`.
- `ipAllowList`: lista de entradas con `source` en notación CIDR. Si se omite, se permiten todas las IP. Las conexiones
  internas de servicios en la misma región siguen funcionando aunque se restrinja el acceso externo.
- Plan `free` aparece como ejemplo de identificador de plan para bases de datos.

**Requiere prueba o confirmación en el panel antes de aplicar:**

1. **`ipAllowList: []`** equivale a "cerrar el acceso externo". La documentación CLI indica que `--clear-ip-allow-list`
   lo deshabilita por completo, pero no confirma la forma YAML. Comprobar tras aplicar con un intento externo, que debe fallar.
2. **Nombre de host privado y puerto de `pserv`.** Confirmar que la API escucha en `$PORT` (así lo hace ahora
   `backend/Dockerfile`) y que `hostport` resuelve a ese puerto.
3. **Identificadores de plan de servicios** (`starter`). No aparecen en la especificación consultada. Confirmarlos en el panel.
4. **Privilegios del usuario por defecto de PostgreSQL.** La documentación consultada no dice si es superusuario, si puede
   crear roles (`CREATEROLE`) ni si es propietario del esquema `public`. Es el punto más importante: ver §4.

## 4. Aprovisionamiento y migración en Render (requiere prueba)

Lo que la migración hace, según el código del repositorio, y qué privilegio exige cada paso:

| Paso | Dónde | Privilegio que exige |
|---|---|---|
| Crear `rckt_schema_owner`, `rckt_migrator`, `rckt_app`, `rckt_operator` | `scripts/db_roles/provision_roles.sql` | `CREATEROLE` |
| `GRANT rckt_schema_owner TO rckt_migrator` | mismo script | Opción de administración sobre el rol |
| `ALTER SCHEMA public OWNER TO rckt_schema_owner` | migración 0009 | Ser propietario del esquema, o superusuario |
| `ALTER DEFAULT PRIVILEGES FOR ROLE rckt_schema_owner ...` | migración 0009 | Actuar como `rckt_schema_owner` |
| `SET LOCAL ROLE rckt_schema_owner` | migración 0009 | Pertenencia del migrador |

**Preflight de solo lectura**, antes de ningún cambio. Ejecutarlo con el usuario de administración de Render:

```sql
SELECT rolname, rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = current_user;
SELECT nspname, pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname = 'public';
SELECT current_setting('server_version');
```

Si `rolcreaterole` es falso, **detenerse**: `provision_roles.sql` no puede ejecutarse allí. Si el propietario de `public` no
puede cederse, también hay que parar. En ambos casos la alternativa es otra instancia PostgreSQL, decisión aparte.

**Lo que no puede saberse sin una instancia real:** si los `ALTER` de la migración 0009 se aceptan con ese usuario. Se
probará en el staging autorizado, en el paso 5 de §5, con la copia de seguridad de §10 tomada antes.

## 5. Orden de la primera puesta en marcha

Cada paso lo ejecuta una persona autorizada y se detiene si el anterior falla.

1. **Aprobar** plataforma, región, coste y tratamiento de datos (§12).
2. **Preflight** de §4. Si falla, parar.
3. **Aplicar el blueprint** `render.yaml`. Los secretos (`sync: false`) quedan vacíos hasta el paso 6.
4. **Aprovisionar roles** con `scripts/db_roles/provision_roles.sql`, con el usuario de administración. El script no define
   contraseñas, aborta si un rol existente tiene SUPERUSER o BYPASSRLS, y no altera roles existentes.
5. **Definir contraseñas** de `rckt_app` y `rckt_migrator` en el gestor de secretos, y crear los logins personales de
   operador (`CREATE ROLE rckt_operator_<persona> LOGIN NOSUPERUSER NOBYPASSRLS INHERIT; GRANT rckt_operator TO ...`).
   Las contraseñas no se escriben en ningún archivo.
6. **Migrar** como `rckt_migrator`, desde `backend/`:
   `DATABASE_MIGRATION_URL="<URL migrador>" python -m alembic upgrade head` y después `python -m alembic current` (debe
   mostrar `0009`). Antes, copia de seguridad (§10).
7. **Verificar catálogo** con las pruebas de §9, la 1. No usar `scripts/db_security_audit.py` sobre staging: exige `_test`.
8. **Configurar** `DATABASE_URL` (`rckt_app`) y el resto de secretos de la API. Desplegarla.
9. **Desplegar el frontend** desde el commit validado (§11). `FASTAPI_INTERNAL_URL` ya se rellena solo con `fromService`.
10. **Crear datos de prueba** (§6) y ejecutar las pruebas de aceptación (§9).

## 6. Lovable Auth: requisitos y verificación

Requisitos a comprobar **en el proyecto de autenticación** (no cambiarlos sin aprobación):

1. **Inicio de sesión por correo** activo.
2. **Clave de firma JWT activa.** Una clave visible en JWKS no demuestra que firme: puede estar en espera o rotada.
3. **Emisor:** `<SUPABASE_URL>/auth/v1`. **Audiencia:** `authenticated`.
4. **JWKS:** `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`. FastAPI acepta solo `ES256` y `RS256`, fijados en el módulo.

Verificación real, solo en un proyecto de desarrollo dedicado:

1. Crear un usuario de prueba. Su contraseña se introduce en el shell, no en un archivo.
2. Ejecutar `scripts/supabase-live-check.py` siguiendo `docs/PHASE2_AUTH_LIVE.md`. Solo imprime PASS/FAIL.
3. Comprobar que el `kid` del token coincide con una clave **activa** de JWKS, y que `alg` es `ES256` o `RS256`.

Estado: **no verificado** en ningún proyecto real. No se afirma que el proveedor que expone Lovable cumpla estos puntos.

## 7. Datos de prueba: organización, dos usuarios y campañas

Sin datos de clientes reales. Nombres ficticios y filas sintéticas.

1. Crear los dos usuarios en el proyecto de autenticación. Anotar su `sub`.
2. Con el login personal de operador:
   `python -m app.cli create-organization --name "Cliente de prueba A" --slug cliente-prueba-a`.
3. `python -m app.cli provision-user --subject <sub> --email <correo ficticio>`.
4. `python -m app.cli grant-membership --organization-slug cliente-prueba-a --role analyst --subject <sub>`.
   Un usuario `analyst` y otro `client_viewer`, en la misma organización.
5. Crear campañas de ejemplo entrando como el analista, con filas sintéticas.

R13: `create-organization` y `provision-user` no generan evento de auditoría. Anotar cada operación a mano hasta resolverlo.

## 8. Pruebas E2E de navegador

Qué hace: compila el frontend en modo Node, prepara la base de prueba y levanta un proveedor OpenRouter simulado, un
emisor de autenticación simulado, FastAPI y el servidor Node en 18080, 18000 y 3100. Playwright usa Chromium. No llama a
OpenRouter, no consume créditos y no usa Lovable Auth.

### Garantías de seguridad de `e2e/prepare_database.py`

La única operación destructiva es `DROP DATABASE`. Se ejecuta **solo** si se cumplen todas estas condiciones, comprobadas antes
de cualquier cambio (`refusal_reasons`, probada en `backend/tests/test_e2e_guard.py`):

1. `E2E_DISPOSABLE_INSTANCE` vale exactamente `yes-this-instance-is-disposable-and-exclusive`.
2. Las cuatro URL apuntan a un host local (`127.0.0.1`, `localhost`, `::1`), al mismo host y puerto.
3. El nombre de la base es exactamente `rckt_e2e_test`. Un sufijo `_test` no basta.
4. El servidor no tiene otras bases de usuario. Una instancia compartida siempre las tiene.
5. Si `rckt_e2e_test` ya existe, lleva el marcador que el propio script escribió al crearla.

Si alguna falla, el script se detiene y nombra solo variables y motivos, nunca URL ni contraseñas.

### Cómo ejecutarlas

Requiere una instancia **dedicada** (contenedor propio o servicio de CI). No puede ser la instancia de desarrollo ni la de staging.

```
docker run -d --name rckt-e2e-disposable --label rckt.purpose=e2e-disposable \
  -e POSTGRES_PASSWORD=<elegir> -p 127.0.0.1:55432:5432 postgres:16-alpine
```

Luego, con las cuatro URL en `E2E_*` y `E2E_DISPOSABLE_INSTANCE` (ver `.env.example`):

```
bun install --frozen-lockfile
python -m pip install -r backend/requirements-dev.txt
bunx playwright install chromium
bun run test:e2e
```

Al terminar, eliminar **solo** ese contenedor (`docker rm -f rckt-e2e-disposable`). No tocar otros contenedores.

Límites en este equipo:

- **Memoria:** al preparar esta fase había 172 MB libres. Ejecutar aquí no es viable. Una sesión anterior fue terminada por
  falta de memoria.
- **Puerto 3100:** debe estar libre.
- **CI:** el job `e2e` de `.github/workflows/docker-ci.yml` aplica las mismas garantías con un PostgreSQL de servicio. Está
  **bloqueado por la facturación de GitHub Actions**: no es una prueba pasada.

Recomendación: ejecutar en un equipo con más memoria, o en CI cuando se resuelva la facturación. Los resultados se
registrarán en `docs/PHASE2_AUTH_RESULTS.md` con comando y salida.

## 9. Pruebas de aceptación

Se ejecutan contra staging tras el paso 10 de §5. Ninguna se da por superada sin evidencia.

1. **Roles:** `SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname LIKE 'rckt%';`. Ninguno con superusuario
   ni BYPASSRLS, salvo el administrador de Render, que no es el runtime.
2. **Migración:** `alembic current` = 0009.
3. **Salud:** `/healthz` de la API responde 200 desde la red privada.
4. **Inicio de sesión:** el analista entra y su sesión se mantiene al recargar.
5. **Aislamiento:** el analista de `cliente-prueba-a` no ve ni modifica datos de otra organización.
6. **Solo lectura:** el `client_viewer` ve campañas y recibe 403 al crear un análisis.
7. **Sin pertenencia:** 403 en cualquier organización.
8. **Token caducado o sin firma válida:** 401.
9. **Análisis real:** **solo** si el propietario aprueba el gasto de OpenRouter. Sin aprobación queda pendiente. No se
   sustituye por una prueba simulada en staging.
10. **Acceso externo cerrado:** una conexión a la base desde fuera de Render falla (§3, punto 1).
11. **Ningún secreto visible:** las respuestas del frontend no contienen claves ni URL internas.

## 10. Rollback seguro

Principio: no se destruyen roles ni datos. Se vuelve a una versión conocida.

1. **Antes de cualquier cambio de esquema:** copia de seguridad (`pg_dump` de la base de staging por el operador, o la copia
   de la plataforma si está contratada). Sin copia, no se migra.
2. **Aplicación:** volver al despliegue anterior desde el panel de Render. Sin `autoDeploy`, el rollback también es manual.
3. **Migración:** `alembic downgrade 0008_audit_events` como `rckt_migrator`. No borra roles ni datos de fundación ni de
   campañas. Ver `docs/PHASE2_DB_RLS.md` §10.
4. **Roles:** no eliminarlos en un rollback. Para retirar un login, `ALTER ROLE ... NOLOGIN` tras revisión.
5. **Verificación:** repetir §9, pruebas 1 a 3.
6. **Registro:** anotar qué se revirtió, cuándo y con qué copia.

## 11. Frontend desplegado desde el código actualizado

- El frontend llama a `POST /api/v1/organizations/{id}/analyses` (`src/lib/campaign-api.functions.ts`). No quedan llamadas
  a `/campaigns/analyze` en `src/` (comprobado con búsqueda).
- Render despliega el commit de la rama indicada. Antes de aplicar, anotar el SHA; tras desplegar, comprobar que el panel
  muestra ese mismo SHA.
- La rama `feat/staging-integration-local` descansa sobre el head del PR #11 (`feat/phase-2-7b2-postgres-rls`). Verificado
  con `git merge-base --is-ancestor`.

## 12. Costos y recursos que requieren aprobación

Ninguno está autorizado. Los precios no se han consultado en el panel y no se citan aquí.

| Recurso | Estado en `render.yaml` | Coste | Decisión |
|---|---|---|---|
| Frontend `web` (`virginia`) | `plan: starter` (**de pago**) | Requiere confirmar precio | Aprobar plan o cambiar a `free` |
| API `pserv` privada (`virginia`) | `plan: starter` (**de pago**) | Requiere confirmar precio y si existe plan gratuito para servicios privados | Aprobar |
| PostgreSQL 16 (`virginia`) | `plan: free` | Sin coste, con límites de Render para instancias gratuitas (duración y almacenamiento a confirmar en el panel) | Aceptar que **no guarda datos que haya que conservar**, o contratar plan de pago |
| Tráfico de red entre servicios y salida | No definido | Requiere confirmar | Aprobar |
| Proveedor de IA (OpenRouter) | Clave no configurada | Coste por token | Aprobar gasto solo para la prueba 9 (§9) |
| Lovable Cloud / Supabase | Sin cambios | — | Ninguna |

**Tratamiento de datos:** la región Virginia (Estados Unidos) almacena los datos de las campañas. Antes de usarla con
datos reales de clientes, revisar si el tratamiento requiere otra región (por ejemplo `frankfurt`) o cláusulas adicionales.
Con datos sintéticos no aplica. Es una decisión de RCKT, no técnica.

## 13. Pendiente antes de la primera puesta en marcha

1. Confirmar precios y planes en el panel de Render (§3, puntos 1 a 4).
2. Preflight de §4 ejecutado y registrado.
3. Verificar Lovable Auth (§6) en un proyecto real.
4. Crear los logins personales de operador.
5. Decidir sobre `client.server.ts` (`SUPABASE_SERVICE_ROLE_KEY`, generado por Lovable): hoy no se importa, vigilado por test.
6. Resolver R13.
7. Investigar la incidencia de cuota por CLI, o documentar que no se reproduce. Ver `docs/PHASE2_DB_RLS.md` §13.11.
8. Ejecutar E2E en un entorno con memoria suficiente o en CI.
9. Verificar privilegios de Lovable Cloud y Supabase (no verificados).

## 14. Autorizaciones necesarias

1. Plataforma, región, planes y coste (§12).
2. Tratamiento de datos en la región elegida.
3. Proyecto de autenticación y usuarios de prueba.
4. Logins personales de operador.
5. Aplicar migraciones en el staging elegido, tras preflight y copia de seguridad.
6. Gasto de OpenRouter, solo para la prueba 9.
7. Publicación de la rama y del PR, ya autorizada para el PR Draft; el merge y el despliegue, no.
