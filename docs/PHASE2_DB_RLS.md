# Fase 2.7B2: implementación de RLS y mínimo privilegio

Estado: implementada y verificada **localmente**, en una instancia PostgreSQL 16 desechable y dedicada a esta fase. No
desplegada. No aplicada en `rckt`, Lovable Cloud ni Supabase Cloud. Rama `feat/phase-2-7b2-postgres-rls`, apilada sobre
`feat/phase-2-7b1-db-security-design`. Diseño de partida: `docs/PHASE2_DB_SECURITY.md`.

## 1. Resultado en una página

| Garantía | Cómo se cumple | Verificación |
|---|---|---|
| El rol de la aplicación no es superusuario, no tiene `BYPASSRLS` y no posee tablas | `scripts/db_roles/provision_roles.sql`; migración 0009 | `test_application_and_operator_roles_are_not_superusers_and_do_not_bypass_rls`, `test_application_and_operator_own_no_table` |
| Sin identidad no hay datos de negocio | RLS sin filas por defecto; `app.subject` ausente = cero filas | `test_without_identity_no_business_row_is_visible` |
| Una organización no ve datos de otra | Políticas por membresía; la API además aplica `require_role` | `test_an_owner_cannot_read_or_touch_campaigns_of_another_organization`, `test_audit_events_from_the_application_are_attributed_and_scoped` |
| El contexto no se filtra entre peticiones ni entre conexiones del pool | `ContextVar` por tarea + `set_config(..., true)` por transacción | `test_identity_is_bound_to_one_task_and_never_crosses_concurrent_requests`, `test_pooled_connection_keeps_no_identity_after_a_session` |
| `REPEATABLE READ` y `READ ONLY` del informe se mantienen con identidad | `session(snapshot=True)`: primer statement de la transacción | `test_snapshot_session_is_repeatable_read_read_only_and_identity_bound` |
| La aplicación no puede `TRUNCATE`, alterar tablas, desactivar disparadores ni crear políticas | Sin privilegios; no es propietaria | `test_application_cannot_truncate_alter_disable_triggers_or_change_policies` |
| La aplicación no modifica ni borra `audit_events` | Sin `UPDATE`/`DELETE`; disparador append-only | `test_application_cannot_update_or_delete_audit_events` |
| El operador no lee tablas; solo ejecuta cuatro funciones acotadas | `SECURITY DEFINER` con `search_path` fijo, `EXECUTE` solo para `rckt_operator` | `test_operator_reads_and_writes_no_table_directly`, `test_operator_functions_are_*` |
| La atribución del operador es verificable sin inventar `actor_user_id` | `audit_events.db_principal = session_user` | `test_operator_events_carry_the_database_login_and_no_invented_actor` |
| Ninguna política permite todo (`USING (true)`) | Revisado con búsqueda en la migración: ninguna | Revisión estática (§6) |

Lo que **no** queda garantizado está en §9.

## 2. Decisiones registradas

**D1. Operador con `rckt_operator` mediante funciones, sin lectura de tablas.** El operador no recibe ningún privilegio
sobre tablas. Cada operación de la CLI es una función `SECURITY DEFINER` que valida sus parámetros, escribe el cambio y su
evento de auditoría en una transacción, y devuelve un resultado mínimo. Esto evita la excepción `USING (true)` que el
diseño dejaba pendiente de aprobación: la lectura por slug, subject o email ocurre dentro de la función, con clave exacta.

**D2. Identidad de la aplicación: `app.subject` local a la transacción, fijado solo tras verificar el token.** Ver §4.

**D3. Atribución del operador humano: `audit_events.db_principal`, con `session_user`.** Cada persona opera con su propio
login de base de datos, miembro de `rckt_operator`. El evento registra ese login. `actor_user_id` sigue nulo para eventos
del operador: no se inventa una identidad de aplicación.

**D4. Ningún `INSERT ... RETURNING` sobre `audit_events` desde la aplicación.** PostgreSQL exige que la fila devuelta sea
visible para el rol que inserta; solo el propietario lee la auditoría. Un `RETURNING` hacía que cada `analysis.created`
fuera rechazado para analistas. Ver §5, hallazgo H2.

**D5. Propiedad de los objetos en `rckt_schema_owner`.** Las migraciones 0001–0008 crearon los objetos como
`rckt_migrator`; 0009 los transfiere. Las funciones y políticas nuevas las crea `rckt_schema_owner` con `SET LOCAL ROLE`.

**D6. El `SECURITY DEFINER` de `rckt_schema_owner` es el único camino de escritura del operador.** Sin `CREATE`, sin
`ALTER`, sin `TRUNCATE`. Cada función: propietario explícito, `search_path = pg_catalog, public`, `EXECUTE` revocado a
`PUBLIC`, parámetros validados, sin SQL dinámico.

## 3. Archivos

| Archivo | Contenido |
|---|---|
| `supabase/migrations/20261029000800_db_security_rls.sql` | SQL portable de 0009 (fuente de verdad) |
| `backend/migrations/versions/20261029_0009_db_security_rls.py` | Revisión Alembic; `STATEMENTS` generado por `scripts/generate_alembic_migration.py 0009`; `downgrade` escrito a mano |
| `scripts/db_roles/provision_roles.sql` | Aprovisionamiento de roles. No es una migración. Sin contraseñas |
| `backend/app/db/session.py` | Contexto de identidad (`bind_identity`, `identity_scope`), `session(snapshot=...)` |
| `backend/app/api/deps.py` | `get_verified_identity` liga el sujeto verificado; `get_db_session` depende de él |
| `backend/app/services/usage_report.py` | Usa `session(snapshot=True)` |
| `backend/app/db/audit.py`, `backend/app/services/audit.py` | Inserción de auditoría sin `RETURNING` |
| `backend/app/db/models.py` | Columna `db_principal` y restricción `operator_principal` |
| `backend/app/cli.py` | Cuatro comandos por funciones; solo `DATABASE_OPERATOR_URL` |
| `backend/app/core/config.py` | `DatabaseSettings.session_role` (solo fixtures de prueba) |
| `backend/tests/test_db_security.py` | 35 pruebas nuevas (§7) |
| `backend/tests/conftest.py`, `test_cli.py`, `test_quota.py`, `test_audit_events.py` | Fixtures de rol y de operador; pruebas de CLI con `DATABASE_OPERATOR_URL` |
| `backend/tests/test_analysis_schema.py`, `test_campaign_schema.py`, `test_persistence.py` | Aserciones reescritas a la regla de 0009 (ver §5) |
| `scripts/security_poc/run_rls_suite.py` | Verificador reproducible con guardas de contenedor |
| `scripts/security_poc/run_poc.py` | Retirado: se niega a ejecutarse (sustituido por el verificador) |
| `scripts/security_poc/poc_policies.sql`, `docs/PHASE2_DB_SECURITY.md` | Diseño y propuesta de 2.7B1 (conservados; ver las notas de estado) |
| `scripts/generate_alembic_migration.py` | Registra la revisión 0009 |
| `supabase/applied-migrations.md` | Registro: 0009 pendiente en los tres entornos |

No se modificaron las migraciones 0001–0008, ni las variables de conexión activas, ni ninguna base de datos fuera de la
instancia desechable.

## 4. Mecanismo de contexto de identidad

**Quién lo fija.** Solo `get_verified_identity`, después de que el verificador acepte la firma, el emisor y la audiencia.
El cliente no envía ningún valor SQL. El sujeto se guarda en un `ContextVar` de la tarea de la petición.

**Cómo llega a la transacción.** `Database.session()` ejecuta `SELECT set_config('app.subject', :s, true)` como primer
statement de la transacción, si hay sujeto ligado. El tercer argumento `true` hace la configuración local a la transacción:
se descarta en `COMMIT` y en `ROLLBACK`. Ninguna función usa `set_config(..., false)`.

**Orden con el snapshot del informe.** `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY` solo puede ser el primer
statement. Por eso `session(snapshot=True)` lo ejecuta antes del `set_config`, dentro de la misma transacción. No se abre
ninguna transacción adicional: la única es la del informe.

**Por qué no hay filtraciones.**
- Un `ContextVar` pertenece a una tarea de asyncio. Cada petición HTTP es una tarea; las peticiones concurrentes no se ven.
  `identity_scope` restaura el valor anterior al salir.
- Un pool con una sola conexión reutiliza la misma conexión física. El valor local a la transacción no sobrevive a la
  transacción siguiente: `test_pooled_connection_keeps_no_identity_after_a_session` lo comprueba con el rol de aplicación.
- Una sesión sin sujeto no fija nada, y las políticas no devuelven filas de negocio: falla cerrada.

**Limitaciones que este mecanismo no resuelve.**
- `app.subject` es una convención de la aplicación, no un secreto. Quien tenga la credencial de `rckt_app` puede ejecutar
  `set_config('app.subject', 'cualquiera', true)` y leer lo que esa identidad vería. RLS protege frente a errores de consulta
  y frente a un código que olvide el filtro; no protege frente a una credencial comprometida. Esa protección depende de
  mantener la credencial de `rckt_app` solo en el contenedor del backend.
- Un fallo de la verificación del token deja la petición sin sujeto, así que no hay datos. Un token válido de otro usuario
  da los datos de ese usuario: es el mismo límite que hoy.

## 5. Hallazgos de la implementación

Los que ya estaban en el diseño se corrigieron antes de codificar. Los siguientes aparecieron al ejecutar; cada uno tiene
su corrección y, cuando aplica, la prueba que lo detecta.

| # | Hallazgo | Causa | Corrección | Detectado por |
|---|---|---|---|---|
| H1 | Alembic no podía transferir la propiedad de objetos de `public` | En PostgreSQL 15 y posteriores `public` pertenece al propietario de la base; el nuevo propietario necesita `CREATE` sobre el esquema | `ALTER SCHEMA public OWNER TO rckt_schema_owner` primero | Probe de sentencias (migración) |
| H2 | Cada `analysis.created` de un analista era rechazado | El `INSERT ... RETURNING` exige que la fila sea visible; la lectura de auditoría es solo del propietario | Inserción sin `RETURNING` (`app/db/audit.py`) | `test_an_analyst_analysis_is_persisted_replayed_without_a_unit_or_a_call` y otras cuatro |
| H3 | El migrador ve todas las filas, sin RLS | Hereda la propiedad de `rckt_schema_owner` (`INHERIT`); PostgreSQL decide el bypass por propiedad heredada | **Riesgo aceptado** (§9). La prueba usa `rckt_app`, no el migrador | `test_pooled_connection_keeps_no_identity_after_a_session`, en una primera versión con el migrador: veía 1 fila sin identidad |
| H4 | Las funciones nuevas siguen siendo ejecutables por `PUBLIC` | `ALTER DEFAULT PRIVILEGES ... REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC` no tiene efecto en esta versión (verificado con una función de prueba: su ACL sigue siendo la de defecto) | Se retiran las dos sentencias de defecto. Cada migración revoca `EXECUTE` a `PUBLIC` en sus funciones, y la prueba de catálogo lo vigila | `test_no_function_in_public_is_executable_by_public`; `test_a_table_created_later_…` |
| H5 | El downgrade dejaba una entrada en `pg_default_acl` | Revertir no puede borrar esa fila sin superusuario | Consecuencia de H4: al retirar las sentencias desaparece también la entrada | Comparación de esquemas en el verificador |
| H6 | `UPDATE` completo sobre `app_users` | Permiso más amplio que el uso real (solo `email`) | `GRANT UPDATE (email)` | Revisión de `information_schema.column_privileges` |
| H7 | Una prueba de la CLI dependía de `cli.insert_audit_event` | La auditoría ya ocurre dentro de la función SQL | La prueba provoca el rechazo con un disparador de prueba que se retira después | `test_a_refused_quota_write_leaves_neither_the_quota_nor_its_event` |
| H8 | Dos aserciones de 0002–0008 afirmaban «RLS sin políticas» | El estado cambia a propósito en 0009 | Aserción reescrita a la regla: toda política de `public` aplica solo a `rckt_app` | `test_analysis_schema`, `test_campaign_schema`, `test_persistence` |

## 6. Revisión estática

- Ninguna política con `USING (true)` o `WITH CHECK (true)` en la migración.
- Ninguna función `SECURITY DEFINER` construye SQL dinámico. Los bloques `EXECUTE format(...)` están en la migración
  (propiedad), no en las funciones de operador.
- `rckt_app`: `SELECT, INSERT, UPDATE` por tabla, salvo `audit_events` (sin `UPDATE`) y `app_users.email`. Ningún `DELETE`,
  `TRUNCATE` ni `REFERENCES` en ninguna tabla.
- `rckt_operator`: ningún privilegio sobre tablas; `EXECUTE` sobre cuatro funciones; `USAGE` sobre `public`.
- Ninguna tabla tiene privilegios para `PUBLIC`, `anon` ni `authenticated`.

## 7. Pruebas

### 7.1 Suite de seguridad: `backend/tests/test_db_security.py`, 35 pruebas, todas aprobadas

Los datos los siembra el rol propietario equivalente (el mismo que usa el resto de la suite). Toda afirmación sobre el
comportamiento de la aplicación o del operador se ejecuta con `rckt_app` o con el login del operador.

- **A. Roles y privilegios (10):** atributos; propiedad; `TRUNCATE`, `ALTER`, políticas y `CREATE` denegados; sin `UPDATE`/
  `DELETE` en auditoría; sin privilegios para `PUBLIC`/`anon`/`authenticated`; el operador no lee ni escribe tablas; las
  funciones de operador no son ejecutables por la aplicación ni por `PUBLIC`; definer con propietario y `search_path` fijos;
  objetos futuros; ninguna función de `public` ejecutable por `PUBLIC`.
- **B. Contexto de identidad (5):** sin identidad no hay filas; aislamiento entre tareas concurrentes; ausencia de contexto
  en el pool; snapshot `REPEATABLE READ`/`READ ONLY` con identidad y escritura denegada; sujeto vacío rechazado.
- **C. Aislamiento por HTTP con `rckt_app` (9):** `GET /me` con varias organizaciones; campañas de otra organización (404);
  roles owner, analyst y client_viewer en campañas; viewer sin análisis y sin llamada al proveedor; análisis, replay sin
  unidad ni llamada; estados de intentos; cuota agotada con 429 sin llamada; auditoría solo para owner; informe solo para
  owner y auditado.
- **D. Ciclo de vida, cuota y auditoría (4):** lease expirado → intentos `unknown` y evento `analysis.failed` interno;
  dos reclamaciones con una unidad → una sola admitida; evento de auditoría rechazado revierte toda la transacción;
  alcance de eventos de la aplicación (actor, organización, tipo).
- **E. Operador (7):** los cuatro comandos con el login del operador; atribución `db_principal` sin `actor_user_id`;
  validaciones de las funciones sin escritura (incluida la cadena de inyección como parámetro); segunda membresía
  rechazada; error de base de datos sin cambios; la CLI no lee `DATABASE_URL` ni `DATABASE_MIGRATION_URL`; el operador no
  lista datos de negocio.

### 7.2 Suite completa del backend

**486 pruebas aprobadas, 0 fallos** (`tests/`, un proceso, secuencial, contra la base desechable). Son las 451 de la fase
2.7A más las 35 nuevas. Ninguna prueba anterior se eliminó: cuatro aserciones de esquema se reescribieron a la regla nueva
(H8) y las pruebas de CLI pasaron a `DATABASE_OPERATOR_URL`.

Limitación explícita: la suite de negocio se ejecuta con el rol propietario equivalente, como antes de 0009. Solo las 35
pruebas de `test_db_security.py` ejercitan RLS con `rckt_app`. Una ejecución completa de toda la suite con `rckt_app` no se
ha hecho; sería la siguiente verificación.

### 7.3 Migración: equivalencia y reversión

Verificador: `scripts/security_poc/run_rls_suite.py --migrations-only`, desde cero, en la instancia desechable.

| Comprobación | Resultado |
|---|---|
| Alembic `upgrade head` (0001–0009) | Correcto |
| SQL portable 0001–0009 en una segunda base | Correcto |
| Esquema Alembic en head frente a SQL portable (volcado `pg_dump -s`, sin `alembic_version`, formato normalizado) | **Idénticos, 669 líneas normalizadas** |
| `alembic downgrade 0008` | Correcto |
| Esquema tras el downgrade frente a SQL portable 0001–0008 | **Idénticos, 463 líneas normalizadas** |
| `upgrade head` tras el downgrade | Correcto |
| Ninguna función de `public` ejecutable por `PUBLIC` tras el upgrade | Correcto |

La normalización ignora solo el formato del volcado (espacios de los cuerpos de función que añade el generador y los tokens
`\restrict`). No ignora sentencias, nombres, privilegios, propietarios ni políticas.

## 8. Entorno desechable y guardas

Instancia creada para esta fase: contenedor `rckt-sec-2-7b2-pg`, imagen `postgres:16-alpine` (PostgreSQL 16.15), etiqueta
`rckt.purpose=db-security-2.7b2`, publicada solo en `127.0.0.1:56413`, sin contraseña (`trust`, solo local). Ningún otro
contenedor se tocó; no se eliminó ningún volumen.

`scripts/security_poc/run_rls_suite.py` no cambia nada sin antes verificar (`verify_container`): que el nombre resuelve a un
único contenedor; que su identificador completo empieza por el fijado en `POC_CONTAINER_ID`; que tiene la etiqueta del
propósito; que no lo gestiona Compose; que la imagen es PostgreSQL 16; que el puerto está publicado solo en `127.0.0.1` y
coincide con `POC_PORT`; y que ningún otro contenedor publica ese puerto. Comprobado con tres negativos: identificador
incorrecto, puerto incorrecto y contenedor inexistente. En los tres se detuvo sin cambios.

Límite de la guarda: un puerto TCP no demuestra qué proceso lo sirve. La guarda se apoya en el identificador fijado y en la
regla de un único publicador; un proceso ajeno que ocupara el puerto sin contenedor no se detectaría. Por eso el verificador
recrea sus bases solo con nombres `rckt_2_7b2_*_test` y nunca elimina contenedores ni volúmenes.

El script destructivo de 2.7B1 (`run_poc.py`) queda retirado: se niega a ejecutarse.

## 9. Riesgos y pendientes

1. **El migrador omite RLS (H3).** `rckt_migrator` es miembro de `rckt_schema_owner` con `INHERIT`, y por eso hereda la
   propiedad de las tablas. Es aceptable mientras el migrador no atienda peticiones ni se use en la CLI: la aplicación no
   tiene su credencial. Mitigación posible: `NOINHERIT` con `SET ROLE` en las migraciones; no hecho porque el downgrade
   necesita reasignar la propiedad, y eso exige rehacer el procedimiento de reversión.
2. **Privilegios de Lovable Cloud y Supabase: no verificados.** Este trabajo no accedió a ningún proyecto gestionado. La
   consulta de solo lectura para comprobarlo está en `PHASE2_DB_SECURITY.md` §10.3. La configuración por defecto de Supabase
   puede conceder privilegios amplios a `anon` y `authenticated`. RLS no impide `TRUNCATE`.
3. **Compatibilidad con Cloud no demostrada.** Crear roles (`CREATE ROLE`), transferir la propiedad del esquema y revocar
   privilegios por defecto pueden estar restringidos en un proyecto gestionado. Si no lo están, el modelo no es aplicable
   tal cual y hay que decidir otra vía.
4. **`PUBLIC` ejecuta las funciones futuras por defecto.** PostgreSQL no permite cambiarlo para las funciones de esta
   versión (H4). La protección es una regla de revisión, reforzada por la prueba de catálogo. Una migración futura que olvide
   revocar `EXECUTE` a `PUBLIC` fallará esa prueba, pero solo si se ejecuta.
5. **Identidad del operador humano: por login, no criptográfica.** `db_principal` registra el login con que se conectó la
   persona. Que ese login pertenezca a una persona concreta depende del procedimiento de alta de logins, que no está
   automatizado.
6. **Auditoría anterior sin atribución.** Los eventos `operator_cli` escritos antes de 0009 tienen `db_principal` nulo. La
   restricción se añadió `NOT VALID` para no validar filas históricas; las nuevas sí se validan.
7. **Coste de RLS no medido.** Las políticas usan subconsultas por fila. No se ha medido con volumen real.
8. **Base de desarrollo `rckt`: no tocada.** Según `supabase/applied-migrations.md`, 0004 y 0005 están aplicadas localmente
   y 0006 a 0008 no. 0009 queda pendiente igual. Aplicarla en `rckt` requiere aprovisionar roles antes y decidir la
   transferencia de propiedad de sus objetos.
9. **El verificador de 2.7B1 no se ejecutó de nuevo.** Queda sustituido. Sus 37 comprobaciones siguen siendo evidencia de
   2.7B1, no de esta fase.
10. **La suite de negocio no usa `rckt_app`.** Ver §7.2.
11. **`app.subject` no es un secreto.** Ver §4.

## 10. Reversión

Procedimiento verificado en la instancia desechable (§7.3):

1. Parar el backend, o desplegar un commit anterior que no use `db_principal` ni el contexto de identidad.
2. `alembic downgrade 0008_audit_events` como `rckt_migrator`. Elimina políticas, funciones de operador y de identidad, la
   columna `db_principal`, los privilegios de `rckt_app` y `rckt_operator`, y devuelve la propiedad a `rckt_migrator`.
3. No borrar roles en un entorno compartido. Desactivar logins con `ALTER ROLE ... NOLOGIN` si hace falta.
4. Verificar con `scripts/db_security_audit.py` (solo lectura) que el estado coincide con el anterior.

El downgrade no toca datos. La entrada de defecto de privilegios de §5 (H4) ya no existe en la reversión.

## 11. Integración futura con Cloud (no hecha)

1. Consulta de solo lectura de privilegios de `anon`, `authenticated` y `PUBLIC` (`PHASE2_DB_SECURITY.md` §10.3). Si hay
   privilegios amplios, corregirlos en una migración nueva antes de cualquier otro paso.
2. Comprobar si el proyecto permite `CREATE ROLE`, `ALTER SCHEMA ... OWNER` y `ALTER TABLE ... OWNER`. Si no, el aprovisionamiento
   se hace por el proveedor o no se hace.
3. Aplicar `provision_roles.sql` con la revisión habitual, y registrar en `applied-migrations.md`.
4. Aplicar la migración 0009 con la revisión habitual, en staging primero.
5. Cambiar `DATABASE_URL` del backend a `rckt_app` solo después de que la suite de seguridad pase contra staging.

## 12. Criterios de aceptación

| Criterio | Estado |
|---|---|
| Roles de mínimo privilegio, sin `SUPERUSER` ni `BYPASSRLS` en los operativos | **Cumplido** en la instancia desechable |
| Políticas RLS que soportan las operaciones reales | **Cumplido**: análisis, intentos, cuota, auditoría, informe, `GET /me`, campañas |
| Aislamiento multi-tenant probado | **Cumplido** con `rckt_app` por HTTP y por servicios |
| Eventos internos y de usuario funcionan | **Cumplido** |
| Cuotas con atomicidad | **Cumplido** (prueba de concurrencia con `rckt_app`) |
| Informe con `REPEATABLE READ` y `READ ONLY` | **Cumplido** |
| CLI funciona con `rckt_operator` | **Cumplido**, las cuatro operaciones |
| Sin `TRUNCATE` ni `BYPASSRLS` en roles ordinarios | **Cumplido** |
| Pruebas relevantes aprueban | **Cumplido**: 486/486 |
| Alembic y SQL portable coherentes | **Cumplido**: 669 y 463 líneas idénticas |
| Reversión segura | **Verificada** en la instancia desechable |
| Suite completa del backend con `rckt_app` | **No cumplido**: solo la de seguridad usa `rckt_app` (§7.2) |
| Privilegios en Lovable Cloud y Supabase | **No verificado** (§9.2) |
| Aplicación en `rckt` o en Cloud | **Fuera de alcance**: no se hizo |

Estado de la fase: **implementada y verificada localmente**, con las dos excepciones marcadas como no cumplidas o no
verificadas. No se declara completada la integración con Cloud.
