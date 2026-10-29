# Fase 2.7B1: seguridad de PostgreSQL y mínimo privilegio

Estado: diseño y prueba de concepto. Rama `feat/phase-2-7b1-db-security-design`, apilada sobre `feat/phase-2-7a-quota-clock`
(PR #9). No cambia roles, políticas ni grants en ninguna base de desarrollo, en Lovable Cloud ni en Supabase. No hay
migraciones nuevas. La implementación queda para 2.7B2.

## 0. Resumen ejecutivo

- **Hoy, en la base local, el proceso FastAPI es superusuario, tiene `BYPASSRLS` y es propietario de las diez tablas
  empresariales.** RLS está activado pero no vincula a ese rol. El aislamiento entre organizaciones depende solo de las
  comprobaciones de la aplicación (`require_role`).
- Ninguna migración crea roles, políticas ni `GRANT`/`REVOKE`. Los privilegios reales de Lovable Cloud y de Supabase no
  están verificados desde el repositorio.
- Se propone un rol de ejecución `rckt_app` sin superusuario, sin `BYPASSRLS` y sin propiedad de tablas, con grants
  mínimos y políticas RLS que dependen de un contexto de identidad fijado por transacción.
- La prueba de concepto en una instancia PostgreSQL 16 desechable confirma el modelo: **37 de 37 comprobaciones pasan**,
  incluido el aislamiento entre organizaciones, `GET /me` con varias organizaciones, la cuota del analista, y las
  restricciones de `TRUNCATE`, `ALTER`, `CREATE POLICY` y escritura sobre `audit_events`.

- **Decisión aprobada (Fase 2.7B1):** la CLI administrativa usa la identidad `rckt_operator`, con permisos explícitos y
  limitados, separada de `rckt_app`, `rckt_migrator` y `rckt_schema_owner`. Quedan descartados `SUPERUSER`, `BYPASSRLS` y
  el uso cotidiano de credenciales del propietario (§2.3).

## 1. Diagnóstico del esquema actual

### 1.1 Lo que se verificó (auditoría de solo lectura, `scripts/db_security_audit.py`)

Ejecutada contra `rckt_test` (base local de pruebas, clúster `rckt-campaign-analyst-db-1`), en transacción de solo lectura.

| Hecho | Resultado |
|---|---|
| Rol que usa la base de pruebas | `rckt`: superusuario, `BYPASSRLS`, con login |
| Roles del clúster (excluyendo `pg_*`) | Solo `rckt`. No existen `anon`, `authenticated` ni `service_role` |
| Propietario de las diez tablas empresariales | `rckt` |
| RLS habilitado | Sí, en las diez tablas |
| `FORCE ROW LEVEL SECURITY` | No, en las diez tablas |
| Políticas (`pg_policy`) | 0 en las diez tablas |
| Privilegios de `PUBLIC` | Ninguno sobre las tablas auditadas |
| `CREATE` en el esquema `public` para `PUBLIC` | No |
| Funciones en `public` | Cinco funciones de disparador, ninguna `SECURITY DEFINER` |
| Disparadores de inmutabilidad | Activos (`enabled = O`) en `analyses`, `analysis_usage` (dos), `audit_events` y `campaign_metric_entries` |

### 1.2 Lo que dice el repositorio (no verificado en vivo)

- `supabase/migrations/*.sql` y `backend/migrations/versions/*.py` no contienen ningún `GRANT`, `REVOKE`, `CREATE POLICY`,
  `CREATE ROLE` ni `SECURITY DEFINER`. Las migraciones 0002 a 0008 solo activan RLS y disparadores.
- `docs/PHASE2_ANALYSES.md` §12 registra que la base local se conecta como `rckt`, con esos tres atributos.
- `docs/LOVABLE_INTEGRATION.md` §2 registra que el cliente generado `client.server.ts` usa `service_role`, que salta RLS.
  Ningún código de la aplicación lo importa.
- `supabase/README.md` §8 afirma que un rol que no es propietario y tiene `SELECT` ve cero filas. Eso es verdad solo con
  RLS sin políticas, y solo si ese rol no tiene `BYPASSRLS`.

### 1.3 Lo que no se puede afirmar

- **Privilegios de `anon` y `authenticated` en Lovable Cloud y en Supabase.** La configuración por defecto de Supabase
  puede conceder privilegios amplios sobre tablas nuevas de `public` a esos roles. Eso es una hipótesis, no un hecho
  verificado aquí. Debe comprobarse con `information_schema.role_table_grants` y `has_table_privilege` (consultas de solo
  lectura, ver §10.3).
- **Roles internos de Supabase** (`supabase_admin`, `authenticator`, `service_role`, `postgres`): no son accesibles desde
  el repositorio, y su alcance real se desconoce.
- **Si el SQL Editor de Lovable ejecuta las migraciones con el mismo rol que usa la aplicación en producción.** Es
  probable, pero no verificado.

## 2. Separación de roles

| Rol | Login | Uso | Atributos | Propiedad y privilegios |
|---|---|---|---|---|
| **rckt_schema_owner** (A) | No (`NOLOGIN`) | Propietario de los objetos de `public`: tablas, funciones y secuencias | `NOSUPERUSER NOBYPASSRLS` | Propietario del esquema. Ninguna sesión de aplicación lo usa |
| **rckt_migrator** (B) | Sí | Solo migraciones (Alembic y SQL portable), con `SET ROLE rckt_schema_owner` | `NOSUPERUSER NOBYPASSRLS`; miembro de `rckt_schema_owner` | No es propietario de tablas. No se usa para peticiones ni para la CLI |
| **rckt_app** (C) | Sí | `DATABASE_URL` del contenedor backend | `NOSUPERUSER NOBYPASSRLS` | No es propietario. Grants mínimos y políticas (§2.1) |
| **rckt_operator** (D) | Sí | Procedimientos administrativos de la CLI (`DATABASE_OPERATOR_URL`) | `NOSUPERUSER NOBYPASSRLS` | No es propietario. Grants por comando (§2.4) |
| **anon / authenticated** (E) | No aplica | Roles de la API de datos de Supabase | — | Sin grants sobre tablas de negocio. Verificado solo sobre roles simulados; no verificado en Cloud |
| **Roles gestionados** (F) | — | `service_role`, `supabase_admin`, `postgres` | Desconocido | Fuera de nuestro control. `service_role` salta RLS: nunca debe llegar al backend ni al navegador |

Nota de nombres: en la prueba de concepto el propietario se llamó `rckt_owner` y tenía login. Corresponde al rol A del
diseño, que es `NOLOGIN`. El script no se modificó para conservar la evidencia.

Los requisitos del encargo se cumplen así:

- **FastAPI no es superusuario ni tiene `BYPASSRLS`.** Verificado en la prueba de concepto (comprobación 1).
- **FastAPI no es propietario de `audit_events` ni de ninguna tabla.** Verificado (comprobación 1 y comprobaciones de `TRUNCATE`).
- **FastAPI no puede hacer `TRUNCATE`, desactivar disparadores ni alterar tablas.** Verificado (comprobación 9).
- **FastAPI no puede modificar migraciones.** No tiene `CREATE` en `public` ni posee tablas. Verificado.
- **Ni `anon` ni `authenticated` reciben privilegios.** Verificado sobre roles simulados.
- **El operador no es superusuario, no tiene `BYPASSRLS`, no posee tablas y no puede hacer `TRUNCATE`.** Por diseño y por
  los grants de §2.4. El rol `rckt_operator` se creó en la prueba de concepto **sin grants ni políticas**: su acceso
  no se probó.
- **Nunca `USING (true)` para la aplicación.** Ninguna política de `rckt_app` lo usa. La única excepción propuesta, para el
  operador, está en §2.5 y requiere aprobación.

### 2.1 Matriz de privilegios propuesta (`rckt_app`)

Sobre las diez tablas. `S` = SELECT, `I` = INSERT, `U` = UPDATE, `D` = DELETE, `T` = TRUNCATE, `R` = REFERENCES.

| Tabla | rckt_app | Condición RLS (lectura / escritura) | Motivo |
|---|---|---|---|
| `app_users` | S, I | Lectura y alta solo de la fila del `subject` verificado | Resolver o aprovisionar la persona actual |
| `organizations` | S | Existe membresía del usuario | Lista de `GET /me` y validación de acceso |
| `organization_memberships` | S | Solo las filas propias | `GET /me` y la validación de rol |
| `campaigns` | S, I, U | Lectura para miembros; alta para owner y analyst; edición solo owner | Requisitos de `campaign_routes` |
| `campaign_metric_entries` | S, I | Lectura para miembros; alta para owner y analyst | Historial inmutable, sin `U` ni `D` |
| `analyses` | S, I, U | Lectura para miembros; alta de owner/analyst como el solicitante; `U` pasa por el disparador de guardia | Ciclo de vida del análisis |
| `analysis_usage` | S, I | Lectura para miembros; alta para owner/analyst | Registro de llamadas |
| `analysis_quotas` | S | **Lectura para todos los miembros** | Ver §4.2 |
| `analysis_quota_usage` | S, I, U | Lectura para miembros; alta y `U` para owner/analyst | Reclamación atómica de unidades |
| `audit_events` | S, I | Lectura solo owner; alta solo de eventos `user` atribuidos al solicitante | Auditoría append-only |

Secuencias: `GRANT USAGE` solo sobre `audit_events_id_seq`, que es la única secuencia de identidad del esquema.
Funciones: `EXECUTE` solo para las dos funciones de ayuda (§5). Esquema `public`: solo `USAGE`.

Sin ningún grant: `TRUNCATE`, `REFERENCES`, `TRIGGER`, `DELETE` sobre `audit_events`, `UPDATE` y `DELETE` sobre
`campaign_metric_entries`, y todo privilegio para `anon`, `authenticated` y `PUBLIC`.

### 2.2 Implicaciones por tipo de objeto

- **Esquemas.** `rckt_app` obtiene `USAGE`. No obtiene `CREATE`. La propiedad de `public` queda en el propietario.
- **Tablas.** Cada grant es explícito. Una tabla nueva no hereda acceso, pero los `ALTER DEFAULT PRIVILEGES` del
  entorno gestionado sí pueden concederlo: habrá que revisarlos (§10.3).
- **Secuencias.** Solo las necesarias para `INSERT` con identidad (`audit_events`).
- **Funciones.** Las ayudas son `SECURITY INVOKER`, con `search_path` fijo y `EXECUTE` revocado a `PUBLIC`. Ninguna
  función de la propuesta es `SECURITY DEFINER`.
- **Futuras tablas.** Cada tabla nueva necesita, en la misma migración: RLS activado, políticas explícitas, grants
  mínimos y una fila en la matriz de este documento. Una prueba de regresión debe fallar si una tabla de negocio no tiene
  RLS o tiene grants a `anon`, `authenticated` o `PUBLIC`.

### 2.3 Decisión aprobada: `rckt_operator`

Las operaciones administrativas de la CLI (`create-organization`, `provision-user`, `grant-membership` y
`set-analysis-quota`) se ejecutan con la identidad **`rckt_operator`**, separada de `rckt_app`, `rckt_migrator` y
`rckt_schema_owner`.

- **Descartado:** `SUPERUSER`, `BYPASSRLS` y el uso cotidiano de las credenciales del propietario de las tablas.
- **Credencial:** `DATABASE_OPERATOR_URL`. No se incluye en el contenedor del backend ni en el `.env` de la aplicación de
  producción; su uso queda limitado a procedimientos del operador. Hoy `cli.py` usa `DATABASE_MIGRATION_URL` si existe y
  `DATABASE_URL` si no (R6). La corrección forma parte de 2.7B2.
- **Trazabilidad:** cada evento que escribe la CLI lleva `source = operator_cli` y `actor_user_id` nulo. La identidad de la
  persona que opera **no se registra**. Es una cuestión abierta para 2.7B2 (R14) y este documento no la resuelve.

### 2.4 Permisos por comando

Derivados de `backend/app/cli.py` y de los repositorios que llama (`OrganizationRepository.create`,
`UserRepository.get_or_create_by_subject`, `MembershipRepository.get_for_user` y `add`, `insert_audit_event`).

| Comando | Lectura | Escritura | Auditoría |
|---|---|---|---|
| `create-organization` | `organizations`: SELECT (por `RETURNING`) | `organizations`: INSERT | Ninguna (R13) |
| `provision-user` | `app_users`: SELECT por `external_subject` | `app_users`: INSERT; UPDATE de `email` si cambia | Ninguna (R13) |
| `grant-membership` | `organizations`: SELECT por slug; `app_users`: SELECT por subject o email; `organization_memberships`: SELECT | `organization_memberships`: INSERT | `audit_events`: INSERT con `membership.granted`, `source = operator_cli` |
| `set-analysis-quota` | `organizations`: SELECT por slug; `analysis_quotas`: SELECT `FOR UPDATE` | `analysis_quotas`: INSERT `ON CONFLICT DO UPDATE` | `audit_events`: INSERT con `quota.updated`, `source = operator_cli` |

PostgreSQL exige privilegio `UPDATE` para `SELECT ... FOR UPDATE` y para `ON CONFLICT DO UPDATE`. Es documentación del
motor, no comprobado en esta fase.

Grants consolidados de `rckt_operator`:

| Tabla | Privilegios | Motivo |
|---|---|---|
| `organizations` | SELECT, INSERT | `create-organization`; búsqueda por slug |
| `app_users` | SELECT, INSERT, UPDATE (solo la columna `email`) | `provision-user`; búsqueda en `grant-membership` |
| `organization_memberships` | SELECT, INSERT | `grant-membership`. Sin UPDATE ni DELETE: la CLI rechaza duplicados y no cambia roles |
| `analysis_quotas` | SELECT, INSERT, UPDATE | `set-analysis-quota` |
| `audit_events` | INSERT, y USAGE en `audit_events_id_seq` | Eventos de los comandos que modifican datos |
| Resto de tablas | Ninguno | El operador no crea análisis, campañas ni uso de cuota |

Denegados explícitamente, y verificables con la consulta de §10.3: `SUPERUSER`, `BYPASSRLS`, propiedad de tablas,
`TRUNCATE`, `DELETE`, `REFERENCES`, `TRIGGER`, `UPDATE` y `DELETE` sobre `audit_events`, y cualquier DDL.

### 2.5 Políticas propuestas para el operador (no probadas)

La prueba de concepto no incluyó políticas para `rckt_operator`. Estas son propuestas para 2.7B2:

- `organizations`: INSERT con `WITH CHECK (status = 'active')`.
- `app_users`: INSERT con `WITH CHECK (external_subject <> '')`; UPDATE de `email` con
  `WITH CHECK (email IS NULL OR email ~ '^[^@\s]+@[^@\s]+$')`.
- `organization_memberships`: INSERT con `WITH CHECK (role IN ('owner', 'analyst', 'client_viewer'))`.
- `analysis_quotas`: INSERT y UPDATE con `WITH CHECK (plan IN ('pilot', 'standard', 'enterprise') AND monthly_analysis_limit >= 0)`.
- `audit_events`: INSERT con `WITH CHECK (source = 'operator_cli' AND actor_user_id IS NULL)`.

**Excepción que requiere aprobación explícita.** Las búsquedas de la CLI (por slug, por `external_subject` y por email)
necesitan leer `organizations`, `app_users`, `organization_memberships` y `analysis_quotas` sin filtro por identidad. Hay
dos caminos:

1. `USING (true)` en esas cuatro tablas, solo SELECT, solo `TO rckt_operator`. Es lo más simple, pero es exactamente el
   patrón que este documento rechaza para la aplicación. Requiere aprobación.
2. Funciones de búsqueda `SECURITY DEFINER`, propiedad de `rckt_schema_owner`, con `EXECUTE` solo para `rckt_operator`,
   `search_path = pg_catalog, public`, que devuelven como mucho una fila por clave exacta. Más código, pero el operador no
   puede listar tablas enteras.

**Recomendación: el camino 2.** No abre lectura masiva, y cumple los criterios de §5.7. Si se elige el 1, la excepción
debe quedar registrada en el documento con su aprobación.

## 3. Riesgos reales

Gravedad: **Crítica** (acceso a datos de otras organizaciones o destrucción de datos, plausible hoy), **Alta** (pérdida de la
garantía de aislamiento o de integridad, requiere condiciones), **Media** (defensa en profundidad o riesgo operativo),
**Baja** (higiene). Los riesgos marcados *no verificado* dependen de datos que no se han podido consultar.

| # | Riesgo | Gravedad | Estado |
|---|---|---|---|
| R1 | El rol de la aplicación es superusuario con `BYPASSRLS` y propietario de todas las tablas. Un fallo de una consulta o una credencial filtrada dan acceso a todas las organizaciones y a la auditoría | **Alta** | Verificado (auditoría) |
| R2 | El propietario puede desactivar el disparador de `audit_events`, así que la inmutabilidad depende de que el rol de la aplicación no sea propietario | **Alta** | Verificado (la prueba de concepto muestra que el rol sin propiedad no puede; con propiedad sí, por definición) |
| R3 | Si `anon` o `authenticated` tienen `TRUNCATE` o `DELETE` sobre tablas de negocio, RLS no lo impide: `TRUNCATE` no pasa por políticas | **Crítica si se confirma** | *No verificado*. Hipótesis por la configuración por defecto de Supabase |
| R4 | El cliente generado de Lovable usa `service_role`, que salta RLS | **Media** | No importado hoy. Riesgo de importación accidental |
| R5 | El aislamiento entre organizaciones descansa solo en la capa de aplicación (`require_role`), sin segunda barrera | **Alta** | Verificado (no hay políticas) |
| R6 | `cli.py` usa `DATABASE_MIGRATION_URL` si existe, y `DATABASE_URL` si no: las operaciones administrativas heredan el rol de migraciones o de la aplicación | **Media** | Verificado en `cli.py`. Corrección en 2.7B2 |
| R7 | El contexto de identidad, si se implementa con variables de sesión, puede fijarlo cualquier código que tenga la credencial de `rckt_app` (§5.4) | **Media** | Limitación de diseño |
| R8 | Si el contexto se fijara en modo sesión (`is_local = false`), se filtraría entre peticiones del pool | **Alta si ocurre** | Mitigado en la propuesta; la prueba de concepto lo demuestra (§7) |
| R9 | Una política mal escrita (`USING (true)` o una recursión entre `organization_memberships` y `organizations`) abriría datos o rompería `GET /me` | **Alta** | Evitado por diseño y probado |
| R10 | La reclamación atómica de cuotas depende de leer el límite en la misma transacción; si el rol no puede leer `analysis_quotas`, el consumo devuelve cero filas y se rechaza como si la cuota estuviera agotada | **Media** | Detectado en la prueba de concepto (§7.2) |
| R11 | Los procesos internos sin usuario (ninguno hoy) no tendrían contexto y no verían datos | **Baja** | Sin procesos batch hoy; ver §5.5 |
| R12 | `DATABASE_URL` y `DATABASE_MIGRATION_URL` pueden apuntar al mismo rol, lo que impide separar migraciones y ejecución | **Media** | Verificado en la configuración local |
| R13 | `create-organization` y `provision-user` no generan eventos de auditoría | **Media** | Verificado en `cli.py` |
| R14 | La persona que opera la CLI no queda registrada: todos los eventos `operator_cli` comparten el mismo rol y no tienen actor | **Media** | Verificado. Pendiente para 2.7B2 |

## 4. Arquitectura propuesta

### 4.1 Principio

Dos barreras independientes. La primera es FastAPI: verifica el token, resuelve la membresía y aplica `require_role`,
como hoy. La segunda es PostgreSQL: el rol `rckt_app` solo ve las filas de las organizaciones donde el usuario verificado
tiene membresía, y solo puede escribir lo que su rol permite. Si la aplicación olvida un filtro, la base devuelve cero
filas en vez de datos ajenos.

### 4.2 Por qué `analysis_quotas` es legible por todos los miembros

La reclamación atómica de cuota ejecuta un `UPDATE` sobre `analysis_quota_usage` con un `FROM analysis_quotas` para
comparar con el límite. Si el rol no puede leer el límite, el `UPDATE` afecta cero filas y la aplicación lo interpreta
como cuota agotada. La prueba de concepto lo confirmó: con la política retirada, el analista obtiene `UPDATE 0`
(§7.2). El límite no es secreto de la organización frente a sus miembros, así que la política de lectura es segura.

### 4.3 Operador CLI (decisión aprobada)

Identidad `rckt_operator`, con permisos explícitos por comando, sin `SUPERUSER`, `BYPASSRLS` ni propiedad de tablas. Detalle en §2.3 a §2.5. La forma de las búsquedas de la CLI (§2.5) queda pendiente de aprobación.

### 4.4 Qué queda fuera de RLS

- `GET /api/v1/organizations/{id}/usage` y `…/audit-events` son solo de owner. La separación la hace `require_role` y
  la política de `audit_events` (solo owner). Los agregados del informe leen tablas de negocio bajo RLS: un analista no
  puede ver el consumo de otra organización aunque la función los pidiera.
- `usage.report_accessed` se registra con `source = user` y `actor_user_id` del owner: la política de inserción lo
  permite porque el actor es el usuario actual.

## 5. Modelo de contexto de identidad

### 5.1 Qué se fija y de dónde

- **Variable:** `app.subject`, texto con el claim `sub` del token ya verificado por `SupabaseTokenVerifier`.
- **Quién la fija:** solo el código de FastAPI, después de verificar el token, en la primera sentencia de cada
  transacción que atienda una petición con identidad.
- **Cómo:** `SELECT set_config('app.subject', $1, true)`. El tercer argumento `true` hace la configuración **local a la
  transacción**: se descarta en `COMMIT` o `ROLLBACK`.
- **Nunca** se fija con `false`, ni desde un parámetro de la petición, ni desde el frontend.

### 5.2 Por qué no `auth.uid()`

`auth.uid()` lee `request.jwt.claims`, que solo existe cuando PostgREST o el cliente de Supabase inyectan el token en la
sesión. Una conexión de FastAPI no pasa por ese camino, así que `auth.uid()` devolvería `NULL` y todas las políticas
cerrarían. Además, Supabase no está disponible en la base local. Por eso el contexto es propio y explícito.

### 5.3 Fail closed

Si `app.subject` no está fijado (`current_setting(..., true)` devuelve `NULL`) o está vacío, ninguna política concede
filas. Las comprobaciones de la prueba de concepto lo confirman para cuatro tablas (§7.1, comprobaciones 2 y 3).

### 5.4 Limitaciones de las variables de sesión personalizadas

- **Cualquiera con la credencial de `rckt_app` puede fijar `app.subject` a cualquier valor.** El contexto no es una prueba
  criptográfica; es una convención que solo el código de FastAPI debe seguir. Frente a un atacante que ya tiene la
  credencial de la aplicación, RLS no añade protección. Su valor es proteger frente a errores de código (consultas sin
  filtro) y frente a una credencial de otro componente que no tenga el mismo contexto.
- **Un contexto en modo sesión se filtra.** Con un pool de conexiones, una configuración de sesión persistiría en la
  siguiente petición. La prueba de concepto lo reproduce (§7.1, check 4) y por eso la propuesta exige `is_local = true`.
- **Un valor de `app.subject` que no corresponda a un usuario** no concede nada: la política de `app_users` solo devuelve
  filas con ese `external_subject`.
- **Otras variables de sesión** (`search_path`, `role`) no deben derivarse de la petición. La conexión usa un rol fijo.

### 5.5 Procesos internos

- Hoy no hay tareas en segundo plano. El abandono de análisis expirados (`abandon_expired`) se ejecuta dentro de una
  petición de un usuario autorizado, así que tiene contexto.
- Si en el futuro se añade un proceso sin usuario (por ejemplo, un expirador de leases), **no** debe reutilizar el rol
  de la aplicación con un contexto inventado. Debe ser un rol propio (`rckt_internal`) con grants mínimos, o ejecutarse
  como operador con el mecanismo de §4.3.

### 5.6 Pool de conexiones

El pool de SQLAlchemy reutiliza conexiones. Por eso:

- La configuración debe fijarse al inicio de **cada** transacción de `Database.session()`, antes de cualquier consulta.
- Las transacciones de solo lectura y las de `/readyz` no fijan identidad; deben devolver cero filas de negocio, y
  `/readyz` solo hace `SELECT 1`.
- Ninguna función debe usar `set_config(..., false)`. Una comprobación de regresión debe buscarlo en el código.

### 5.7 Funciones SECURITY DEFINER

**No se propone ninguna en la fase 2.7B2.** Las dos funciones de ayuda son `SECURITY INVOKER` y no necesitan más
privilegios que los del rol. Si en el futuro se necesitara una (por ejemplo, para resolver `app_users` en el primer
acceso sin conceder `SELECT` amplio), debería cumplir: propietario `rckt_schema_owner` (sin login), no superusuario, `EXECUTE` solo para
`rckt_app`, `search_path = pg_catalog, public` y cuerpo que no acepte identidades arbitrarias.

### 5.8 Recursión

Cadena de dependencias, sin ciclos:

`app_users` (depende solo de `app.subject`) ← `organization_memberships` (depende de `app_users`) ← `organizations`,
`analyses`, `campaigns`, etc. (dependen de `organization_memberships`).

Ninguna política de `organization_memberships` consulta `organizations` u otras tablas con RLS.

### 5.9 Por qué se descartan alternativas

- Políticas basadas en `auth.uid()`: no disponibles en conexiones de FastAPI.
- Filtrar solo en la aplicación: ya existe y no protege de errores de consulta.
- Un rol por organización: no escala ni se alinea con el modelo de membresías.
- Políticas recursivas sobre `organization_memberships`: rompen en PostgreSQL con error de recursión.

## 6. Compatibilidad con FastAPI y servicios internos

Verificado en la prueba de concepto, con las consultas tal como las ejecuta la aplicación:

- **`GET /api/v1/me`**: la consulta de membresías del usuario y la de organizaciones devuelven exactamente las filas
  propias (dos organizaciones para un usuario con dos; una para uno con una). Ver §7.1, comprobación 7.
- **Resolución de `app_users`** (`get_or_create_by_subject`): el `SELECT` por `external_subject` y el `INSERT` (que hace
  `ON CONFLICT DO NOTHING` en el repositorio) funcionan porque la política compara con el mismo `subject`.
  *No probado*: el `ON CONFLICT` con política de `INSERT` en PostgreSQL requiere que la fila insertada cumpla
  `WITH CHECK`; hay que probarlo en 2.7B2.
- **Validación de membresía y rol** (`require_role`): ver §7 (aislamiento y roles).
- **Análisis**: alta por owner y analyst como solicitante; lectura para miembros; la guardia de cambios sigue siendo un
  disparador. *No probado* el `UPDATE` de finalización (`analyses_writer_update`); es la siguiente prueba a añadir.
- **Cuotas**: reclamación con `UPDATE … FROM analysis_quotas` (§4.2), verificada para analista y para cliente-lector.
- **Auditoría**: inserción de eventos `user` atribuidos; lectura solo owner. *No probado*: la inserción de eventos
  `internal` y `operator_cli` (que la política rechaza para `rckt_app`, correcto, pero el operador no está diseñado).
- **Informe de consumo**: lecturas agregadas bajo RLS. *No probado* con el informe completo; la prueba de 2.7B2 debe
  comparar el resultado de `usage_report()` con y sin el rol de aplicación.

## 7. Prueba de concepto y pruebas de seguridad

### 7.1 Ejecución

Instancia desechable: `postgres:16-alpine` (PostgreSQL 16.15), creada para esta prueba en `127.0.0.1`, sin contraseña
(`POSTGRES_HOST_AUTH_METHOD=trust`, solo local), separada del clúster del proyecto y de los de otros proyectos. Se creó un
contenedor nuevo con el nombre `rckt-sec-poc-pg`. No se usó Supabase Cloud, Lovable Cloud ni `rckt`.

Las migraciones portables de `supabase/migrations/` se aplicaron como propietario `rckt_owner` en `rckt_poc_test`, en
orden. Después se aplicó `scripts/security_poc/poc_policies.sql`, y se sembraron dos organizaciones con cinco usuarios.

Comando: `POC_PORT=56412 python scripts/security_poc/run_poc.py`. Resultado: **37 passed, 0 failed**.

Las comprobaciones, agrupadas:

1. **Atributos del rol:** `rckt_app` no es superusuario, no tiene `BYPASSRLS` y no es propietario de tablas.
2. **Sin identidad:** `organizations`, `campaigns`, `audit_events` y `analyses` devuelven cero filas.
3. **Contexto transaccional:** el contexto no sobrevive a la transacción siguiente en la misma conexión.
4. **Diagnóstico de la fijación de sesión:** con `is_local = false` el contexto sí se filtra. Demuestra por qué la
   propuesta exige `true`. Después se limpia.
5. **Aislamiento entre organizaciones:** el owner de A ve solo A; no lee campañas de B; no inserta en B; sí inserta en A.
6. **Permisos por rol:** `client_viewer` lee campañas de A, pero no crea campañas ni análisis. El analista no lee
   `audit_events`; el owner de A sí lee los de A y no los de B.
7. **`GET /me`:** un usuario con dos organizaciones ve sus dos membresías y las dos organizaciones; un usuario con una ve
   solo la suya.
8. **Cuota:** el analista consume una unidad con el `UPDATE … FROM` (`UPDATE 1`); el cliente-lector no consume
   (`UPDATE 0`).
9. **Límites del rol:** no puede `TRUNCATE` (ni `campaigns` ni `audit_events`), desactivar el disparador, alterar una
   tabla, actualizar o borrar `audit_events`, crear políticas, crear tablas en `public` ni asumir `rckt_owner`. Sin grants
   para `anon` ni `authenticated`.
10. **Escritura autorizada:** un evento `user` atribuido al propio usuario en su organización se acepta; uno atribuido a
    otra persona, y uno `internal` desde el rol de aplicación, se rechazan.

Nombres: en esta prueba el propietario se llama `rckt_owner` y tiene login; en el diseño corresponde a `rckt_schema_owner` (sin login) y `rckt_migrator`. El script no se cambió para no invalidar la evidencia. `rckt_operator` se creó sin grants ni políticas, y no se probó.

### 7.2 Control negativo

Se retiró temporalmente la política `analysis_quotas_member_select`. Con ella retirada, el analista no ve el límite
(`visible_limits = 0`) y el consumo devuelve `UPDATE 0`, lo que la aplicación interpretaría como cuota agotada. La
política se restauró después. Esto confirma que la comprobación 8 depende realmente de la política y que R10 es real.

### 7.3 Qué NO quedó verificado

- **Roles reales de Supabase.** `anon` y `authenticated` se crearon como roles sin login, sin los grants por defecto de
  Supabase. La comprobación 9 demuestra que la propuesta no concede nada a esos roles, no que el proyecto gestionado esté
  libre de grants.
- **Procesos de finalización y abandono** (`UPDATE` de `analyses` por el ciclo de vida): no probados.
- **Rol operador:** no diseñado ni probado.
- **Pool real de SQLAlchemy y uvicorn:** la prueba usa `asyncpg` directo; el comportamiento con el pool de la aplicación
  se prueba en 2.7B2.
- **Coste de planificación:** no medido. Las funciones `STABLE` con subconsultas bajo RLS deben medirse con el volumen
  real antes de producción.
- **Lectura de `GET /usage` y de la auditoría por la aplicación real:** no ejecutada contra el backend.

### 7.4 Limpieza

La instancia desechable se elimina al terminar (`docker rm -f rckt-sec-poc-pg`). No quedan roles ni bases en otras
instancias. El clúster del proyecto no se modificó.

## 8. Matriz de pruebas de seguridad

Columna **PoC** = verificado en la prueba de concepto. Columna **2.7B2** = prueba que debe existir en la siguiente
subfase, contra PostgreSQL real y con la aplicación.

| Caso del encargo | PoC | 2.7B2 |
|---|---|---|
| Usuario de A no consulta datos de B | Sí (5) | Sí, también por la API |
| Miembro sin permiso no accede a costes administrativos | Parcial: el analista no lee la auditoría (6). El consumo no está en la PoC | Sí, con el informe |
| `client_viewer` no genera análisis ni modifica campañas | Sí (6) | Sí, por la API |
| Transacción sin contexto no obtiene datos de negocio | Sí (2) | Sí, en `/readyz` y rutas sin identidad |
| Contexto residual no se reutiliza | Sí (3) | Sí, con el pool de la aplicación |
| El rol de aplicación no puede `TRUNCATE` | Sí (9) | Sí, en el rol de producción |
| No puede desactivar disparadores ni alterar tablas | Sí (9) | Sí |
| No puede modificar ni borrar `audit_events` | Sí (9) | Sí |
| Puede insertar eventos autorizados | Sí (10) | Sí, desde los servicios |
| Puede completar cuota, análisis y auditoría | Parcial: cuota sí; análisis y finalización no | Sí, flujo completo |
| `GET /me` con varias organizaciones | Sí (7) | Sí, por la API |
| Acceso del operador CLI diferenciado | No diseñado | Sí, cuando se decida §4.3 |

Pruebas adicionales para 2.7B2: que `DATABASE_URL` apunte a `rckt_app` en CI con la base de pruebas; que ninguna
función del repositorio emita `set_config(..., false)`; que una migración nueva no conceda privilegios a `anon`,
`authenticated` ni `PUBLIC`; y que una tabla de negocio nueva sin RLS haga fallar una prueba.

Las pruebas de aislamiento y autorización existentes (`test_campaign_api`, `test_analysis_api`, `test_usage_report`,
`test_audit_events`, `test_quota`) se conservan. Se ejecutan con el rol propietario, así que no prueban RLS; por eso 2.7B2
debe añadir una variante que las ejecute con `rckt_app`.

## 9. Plan de implementación de 2.7B2

Alcance: un solo cambio de seguridad, con prueba y procedimiento de reversión. No incluye roles de Lovable ni de Supabase.

### 9.1 Archivos a modificar

- `backend/app/db/session.py`: `Database.session()` acepta una identidad opcional. Si la hay, ejecuta
  `set_config('app.subject', $1, true)` como primera sentencia de la transacción. Sin identidad, no fija nada.
- `backend/app/api/deps.py`: `get_db_session` recibe la identidad verificada y abre la sesión con contexto.
  `get_current_user` resuelve el usuario con el mismo contexto.
- `backend/app/db/repositories.py`: sin cambios de lógica. Revisar que `get_or_create_by_subject` cumple la política de
  inserción.
- `backend/app/cli.py`: usa una variable nueva `DATABASE_OPERATOR_URL` (decisión §4.3). Mientras no exista, el comportamiento
  no cambia.
- `backend/app/core/config.py`: nombres de variables nuevas, sin cambiar las activas.
- Pruebas: `backend/tests/test_db_security.py` (nuevo), que se ejecuta solo si existe `DB_SECURITY_TEST_URL` apuntando a
  una base `_test` con el rol `rckt_app`.

### 9.2 Migraciones incrementales

- **Nueva migración `0009`** (Alembic y SQL portable en `supabase/migrations/`). Contenido: funciones de ayuda, grants
  mínimos, RLS ya activo (no cambia), políticas del §2.1. **No crea roles.**
- Motivo de no crear roles en la migración: `CREATE ROLE` es de clúster; Supabase y Lovable pueden no permitirlo a
  migraciones; y la contraseña no debe estar en el repositorio. Los roles se aprovisionan con un script separado,
  ejecutado una vez por entorno (`scripts/provision_db_roles.sql`, a escribir), que comprueba que no existen y no cambia
  roles existentes.
- Las migraciones 0001 a 0008 no se modifican.

### 9.3 Provisionamiento de roles

- Creación de `rckt_schema_owner` (`NOLOGIN`), `rckt_migrator`, `rckt_app` y `rckt_operator`, si no existen, todos con `NOSUPERUSER NOBYPASSRLS`. `rckt_migrator` pasa a ser miembro de `rckt_schema_owner`. `rckt_operator` recibe solo los grants de §2.4.
- Asignación de la contraseña fuera del repositorio.
- Verificación de atributos con la consulta de §10.3.

### 9.4 Conexiones

- `DATABASE_URL` (aplicación) → `rckt_app`. **Cambio de variable en el entorno de producción, no en el repositorio.**
- `DATABASE_MIGRATION_URL` → `rckt_migrator`.
- `DATABASE_OPERATOR_URL` → `rckt_operator` (nueva). La CLI deja de usar `DATABASE_MIGRATION_URL`.
- En desarrollo local, mantener `rckt` mientras no se aplique la migración, para no romper la base actual.

### 9.5 Pruebas de compatibilidad

- Toda la suite existente debe seguir pasando con el rol propietario (no cambia el comportamiento de negocio).
- La suite nueva debe pasar con `rckt_app`.
- `GET /me`, análisis, cuota, auditoría e informe: pruebas de extremo a extremo con `rckt_app` en la base de pruebas.
- Se mide el coste de las consultas con RLS en un conjunto de datos representativo antes de declarar la fase completa.

### 9.6 Qué es estándar y qué depende de la plataforma

| Operación | Estándar PostgreSQL 13+ | Depende de Lovable o Supabase |
|---|---|---|
| `CREATE POLICY`, `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` | Sí | No |
| `GRANT` / `REVOKE` sobre tablas y secuencias | Sí | No (los roles deben existir) |
| `CREATE ROLE` | Sí, si se tiene `CREATEROLE` | **Sí**: puede estar restringido en el entorno gestionado |
| `set_config(..., true)` | Sí | No |
| Revocar privilegios por defecto de `anon` / `authenticated` (`ALTER DEFAULT PRIVILEGES`) | Sí | **Sí**: depende de la configuración del proyecto |
| `auth.uid()`, `request.jwt.claims` | No se usan | Dependen de Supabase; descartados |
| `BYPASSRLS` para `service_role` | No | **Sí**: el rol de servicio de Supabase lo tiene |

### 9.7 Procedimiento de reversión

1. Restaurar `DATABASE_URL` al rol propietario en el entorno afectado. Es un cambio de variable, sin despliegue de código.
2. Si la migración `0009` debe revertirse: `DROP POLICY` de cada política, `REVOKE` de los grants y `DROP FUNCTION` de las
   dos ayudas. Es un `downgrade` de Alembic y el SQL inverso equivalente, sin tocar datos.
3. No borrar roles en un entorno compartido. Desactivar el login (`ALTER ROLE ... NOLOGIN`) si hace falta.
4. Verificar con `scripts/db_security_audit.py` que el estado coincide con el anterior.

### 9.8 Integración con Cloud (futura, no en 2.7B2)

1. Comprobar con consultas de solo lectura los privilegios de `anon`, `authenticated` y `PUBLIC` (§10.3).
2. Comprobar si el proyecto permite `CREATE ROLE` y `ALTER DEFAULT PRIVILEGES`. Si no, el modelo de roles de aplicación no
   es aplicable tal cual y hay que decidir otra vía.
3. Aplicar el script de roles y la migración `0009` a mano, con la revisión habitual (`applied-migrations.md`).
4. Cambiar `DATABASE_URL` de la aplicación solo después de comprobar en staging que las pruebas pasan.

## 10. Criterios de aceptación, rollback y consultas de solo lectura

### 10.1 Criterios de aceptación para 2.7B2

- Ninguna tabla de negocio tiene grants a `anon`, `authenticated` ni `PUBLIC`.
- `rckt_app` no es superusuario, no tiene `BYPASSRLS` y no es propietario de tablas.
- Las 37 comprobaciones de la prueba de concepto pasan con la aplicación real y la base `rckt_test`.
- `GET /me`, análisis, cuota, auditoría e informe pasan sus pruebas con `rckt_app`.
- Ninguna función del repositorio usa `set_config(..., false)`.
- La migración `0009` tiene su SQL portable equivalente, verificado con `pg_dump -s`, como exige `supabase/README.md`.
- Las migraciones 0001 a 0008 no cambian.
- El operador CLI tiene una credencial separada, o la decisión §4.3 queda documentada como excepción.

### 10.2 Criterios de rollback

- La reversión de §9.7 se ejecuta sin pérdida de datos.
- Tras el rollback, la suite existente sigue pasando con el rol propietario.

### 10.3 Consultas de solo lectura para verificar en Cloud

Ejecutables sin modificar nada. Se pegan en el SQL editor del entorno que se quiera comprobar. No contienen credenciales.

```sql
-- Grants directos sobre tablas de negocio a roles de API (debe devolver 0 filas)
SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND grantee IN ('anon', 'authenticated', 'PUBLIC')
  AND table_name IN ('organizations', 'app_users', 'organization_memberships', 'campaigns',
                     'campaign_metric_entries', 'analyses', 'analysis_usage', 'analysis_quotas',
                     'analysis_quota_usage', 'audit_events');

-- Atributos del rol actual (nunca mostrar credenciales)
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin
FROM pg_roles WHERE rolname = current_user;

-- RLS y FORCE por tabla de negocio
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname;

-- Privilegios por defecto que se aplicarían a tablas nuevas de public
SELECT pg_get_userbyid(defaclrole) AS creator, defaclobjtype, defaclacl
FROM pg_default_acl;
```

Herramienta local, sin credenciales en el repositorio: `scripts/db_security_audit.py`, que requiere `DATABASE_AUDIT_URL`
apuntando a una base cuyo nombre termine en `_test`.

## 11. Dependencias y decisiones pendientes

1. **Mecanismo del operador CLI: resuelto** (§2.3). Pendientes para 2.7B2: la identidad humana del operador (R14) y la forma de las búsquedas de la CLI (§2.5).
2. **Verificar en Lovable y en Supabase** los privilegios de `anon`, `authenticated` y `PUBLIC` (R3). Es la comprobación de
   mayor prioridad, y no la puedo hacer desde aquí.
3. **Confirmar si el proyecto gestionado permite `CREATE ROLE`** y `ALTER DEFAULT PRIVILEGES`.
4. **Decidir si `DATABASE_URL` de producción pasa a `rckt_app`** antes de la fase piloto.
5. **Definir la política de `service_role`**: que nunca se importe en el backend ni en el frontend. Un chequeo automático
   en CI lo garantizaría.
6. **Probar la finalización de análisis y el informe** con `rckt_app` (§6).
7. **Medir el coste** de las políticas con volumen real.
8. Pendientes heredados que no cambian con esta fase: verificación en vivo de la identidad, `platform_admin` (no
   implementado), CLI sin atribución de persona (R6), y la auditoría de `campaigns` y del aprovisionamiento.

## 12. Entregables de esta fase

- `docs/PHASE2_DB_SECURITY.md` (este documento).
- `scripts/db_security_audit.py`: auditoría de solo lectura de catálogos. No escribe, no crea roles, no imprime URLs.
- `scripts/security_poc/poc_policies.sql`: propuesta de roles, grants y políticas. **No es una migración.** Solo se aplica a
  una instancia desechable.
- `scripts/security_poc/run_poc.py`: prueba de concepto de 37 comprobaciones. Se conecta solo a `127.0.0.1` y a la base `rckt_poc_test`; ambos valores son constantes del código, sin comprobación en tiempo de ejecución para otros valores. Requiere `POC_PORT`.

Ninguna de estas piezas se ejecutó contra `rckt`, Lovable Cloud ni Supabase Cloud. La única base que recibió `CREATE ROLE`
y políticas fue la instancia desechable, que se eliminó.
