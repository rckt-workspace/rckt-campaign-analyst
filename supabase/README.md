# Migraciones SQL portables (Lovable Cloud y Supabase)

Esta carpeta contiene el esquema empresarial como SQL plano de PostgreSQL. Sirve para aplicarlo a mano en el SQL Editor de Lovable Cloud y, más adelante, en un Supabase licenciado. Es el **mismo esquema** que mantiene Alembic en `backend/migrations`, y las dos cosas se verifican entre sí (sección 5).

Las migraciones las ejecuta el responsable de RCKT, a mano, en Lovable Cloud o en Supabase, después de revisar los archivos. Este repositorio nunca ejecuta SQL contra una base remota.

Estado conocido (informado por el responsable): las migraciones `20261025000100_foundation.sql` y `20261025000200_campaigns.sql` fueron aplicadas manualmente en Lovable Cloud y verificadas mediante consultas SQL. Las fechas no se registraron; ver `applied-migrations.md`.

## 1. Orden de migraciones

| Orden | Archivo | Contenido | Equivalente Alembic |
|---|---|---|---|
| 1 | `migrations/20261025000100_foundation.sql` | Tablas `organizations`, `app_users`, `organization_memberships`; restricciones, índice, RLS activado sin políticas | `0001_foundation` + `0002_constraint_names_and_rls` |
| 2 | `migrations/20261025000200_campaigns.sql` | Tablas `campaigns` y `campaign_metric_entries`; claves compuestas por organización, historial inmutable, RLS activado sin políticas | `0003_campaigns` |

Las migraciones se ejecutan **en ese orden y una sola vez por base de datos**. La 2 requiere la 1. Sin `IF NOT EXISTS` a propósito: si ya existe alguna tabla, el script falla y no sobrescribe nada.

Archivos de apoyo, que **no** se ejecutan para aplicar cambios:
- `verification.sql`: consultas de solo lectura para comprobar el resultado.
- `applied-migrations.md`: registro manual de qué se aplicó, dónde y cuándo.

## 2. Requisitos del servidor

- PostgreSQL 13 o superior. `gen_random_uuid()` viene integrado desde la versión 13 y no requiere extensiones. El script se detiene con un error explícito si la versión es anterior.
- Sin extensiones, sin funciones propias, sin `auth.uid()`, sin referencias a `auth.users`. Así el mismo archivo sirve en cualquier PostgreSQL 13+.
- Sin identificadores de proyecto, claves ni contraseñas dentro del SQL.

## 3. Procedimiento en Lovable Cloud (SQL Editor)

Hipótesis a confirmar, no verificada: el SQL Editor de Lovable Cloud acepta un script con varias sentencias y `BEGIN`/`COMMIT`. (La migración de fundación se ejecutó correctamente con este procedimiento, según informó el responsable.)

Para la migración de campañas (`20261025000200_campaigns.sql`):
1. Confirmar que la fundación está aplicada: `verification.sql`, consultas 1 a 7 (las tres tablas existen, RLS activo, nombres de restricciones correctos).
2. Confirmar que **no existen** `campaigns` ni `campaign_metric_entries` (ambas deben devolver 0 filas en la consulta 1 de la sección de campañas de `verification.sql`).
3. Ejecutar el archivo completo **una vez**.
4. Ejecutar la sección de campañas de `verification.sql` y registrar el resultado.

1. Confirmar que la base de datos de Lovable Cloud está habilitada y que la versión de PostgreSQL es 13 o superior (`SELECT version();`).
2. Confirmar que **no existen** las tablas `organizations`, `app_users` ni `organization_memberships` (`verification.sql`, consulta 1, debe devolver 0 filas).
3. Abrir el SQL Editor, pegar el contenido completo de `migrations/20261025000100_foundation.sql` y ejecutarlo **una vez**.
4. Ejecutar `verification.sql` completo y comparar con los resultados esperados que describe cada consulta.
5. Registrar la aplicación en `applied-migrations.md`.

Si el script falla a mitad, la transacción se revierte por el `BEGIN`/`COMMIT`. Revisar el error antes de reintentar.

## 4. Procedimiento en Supabase licenciado (futuro)

**Importante, ya aplicable a Lovable Cloud:** las migraciones de esta carpeta fueron ejecutadas a mano. Eso no equivale a un registro en el historial de Supabase CLI ni en ninguna otra herramienta. Este repositorio contiene `supabase/config.toml` (generado por Lovable), así que el CLI reconoce `supabase/migrations/` como su propia carpeta de migraciones. Antes de usar `supabase db push` en esa base, hay que revisar el estado del historial del CLI, porque podría intentar reaplicar los archivos ya ejecutados.

Mismo procedimiento que en Lovable Cloud, en el SQL Editor del proyecto licenciado.

Si se decide usar **Supabase CLI** con su carpeta de migraciones, hay que tener en cuenta:
- `supabase db push` registra cada archivo en su tabla de historial interna (`supabase_migrations.schema_migrations`). Un archivo aplicado a mano **no aparece** ahí, y el siguiente `db push` puede intentar ejecutarlo de nuevo. Si se aplica a mano, se debe registrar también con `supabase migration repair` (comando a confirmar con la versión de la CLI en uso en ese momento).
- No ejecutar `supabase db reset` contra un proyecto con datos reales.

## 5. Coherencia con Alembic

Mapa de revisiones: `0001_foundation` y `0002_constraint_names_and_rls` equivalen a `migrations/20261025000100_foundation.sql`; `0003_campaigns` equivale a `migrations/20261025000200_campaigns.sql`.

Lección aprendida: la convención de nombres de SQLAlchemy antepone `ck_<tabla>_` a los nombres de `CHECK`. Las migraciones de Alembic deben usar nombres cortos (como los modelos), no nombres completos. Dos revisiones anteriores lo incumplieron y generaron nombres duplicados; `0002` y las pruebas de `test_persistence.py` lo corrigen y lo vigilan.

Hay una sola fuente de verdad conceptual: los modelos de SQLAlchemy y las migraciones de Alembic en `backend/`. El SQL de esta carpeta es una **traducción manual** de ese estado, y se mantiene igual con este procedimiento:

1. Cualquier cambio de esquema se hace primero como migración de Alembic y su modelo, con sus pruebas.
2. Después se añade un archivo SQL nuevo en esta carpeta con el siguiente prefijo de fecha, y se documenta aquí su equivalencia.
3. La equivalencia se comprueba así: aplicar Alembic en una base vacía, aplicar el SQL en otra base vacía, y comparar con `pg_dump -s`. Hoy el resultado es idéntico salvo las líneas `\restrict` / `\unrestrict`, que son tokens aleatorios que añade `pg_dump`.
4. `verification.sql` debe dar la misma salida en ambas bases.

No se eliminan las migraciones de Alembic. Son las que usa el backend local y las pruebas.

## 6. Relación con Drizzle y el historial de Lovable

Lovable puede mantener sus propias migraciones bajo `drizzle/migrations`. Este repositorio no contiene esa carpeta hoy.

- Ejecutar SQL a mano **no actualiza** el historial de Drizzle. Si más adelante Lovable genera migraciones de Drizzle que tocan las mismas tablas, habrá que reconciliarlas a mano.
- No mezclar en el mismo proyecto dos herramientas de migración sobre las mismas tablas. Elegir una por entorno y documentarla en `applied-migrations.md`.
- Nunca editar las tablas internas de historial de Drizzle, de Supabase CLI ni de Alembic (`alembic_version`) para "arreglar" un registro. Se corrige el registro con la herramienta que lo creó.

## 7. Advertencias

- **No ejecutar el mismo archivo dos veces en la misma base.** La segunda ejecución falla con `relation "organizations" already exists`, y ese fallo es la protección. Verificar `applied-migrations.md` antes de ejecutar.
- No ejecutar nada contra una base remota sin revisión previa del archivo.
- No crear políticas RLS permisivas (`USING (true)`) para "hacer funcionar" una API. Ver sección 8.

## 8. Seguridad: RLS

RLS está **activado** en las tres tablas y **no hay políticas**. Consecuencias verificadas en PostgreSQL local (`rckt_sql_fresh_test`):

- El propietario de las tablas (el rol que usa FastAPI) ve todas las filas. Esto funciona porque RLS no se fuerza al propietario (`FORCE` no está activo).
- Un rol que no es propietario y que tenga `SELECT` concedido ve **0 filas**.

Esto es una postura conservadora. No supone que `auth.uid()` funcione de una forma concreta, porque no se ha verificado.

Políticas que habrá que escribir **después** de confirmar la integración de identidad (no antes):

| Tabla | Acceso previsto | Condición (a verificar) |
|---|---|---|
| `organizations` | Lectura por miembros | Existe membresía del usuario autenticado en la organización |
| `organization_memberships` | Lectura de las propias membresías | `user_id` corresponde al usuario autenticado |
| `app_users` | Lectura del propio registro | `external_subject` corresponde al usuario autenticado |
| todas | Escritura | Ninguna para roles de API; las escrituras pasan por FastAPI o por la CLI de operador |
| `campaigns` | Lectura por miembros de la organización | Igual que `organizations`, vía `organization_id` |
| `campaign_metric_entries` | Lectura por miembros; inserción por analistas y propietarios | Igual, más rol `owner` o `analyst` para insertar (ver sección 11) |

Antes de escribir cualquier política, comprobar en la base real cómo se resuelve la identidad del usuario que hace la petición. No suponer que coincide con `auth.uid()`.

## 9. Exportación e importación de datos

Los datos existentes (organizaciones, usuarios y membresías de prueba) se trasladan conservando los UUID y el orden de las claves foráneas. Los identificadores UUID no dependen de secuencias, así que no hay que reajustar secuencias.

Orden obligatorio, porque las claves foráneas lo exigen: `organizations`, después `app_users`, después `organization_memberships`.

Exportar (en el entorno de origen, solo lectura):

```sql
\copy (SELECT * FROM public.organizations ORDER BY id) TO 'organizations.csv' CSV HEADER
\copy (SELECT * FROM public.app_users ORDER BY id) TO 'app_users.csv' CSV HEADER
\copy (SELECT * FROM public.organization_memberships ORDER BY id) TO 'organization_memberships.csv' CSV HEADER
```

Importar (en el destino, con la tabla recién creada y vacía):

```sql
BEGIN;
\copy public.organizations FROM 'organizations.csv' CSV HEADER
\copy public.app_users FROM 'app_users.csv' CSV HEADER
\copy public.organization_memberships FROM 'organization_memberships.csv' CSV HEADER
COMMIT;
```

Después, ejecutar `verification.sql` y comprobar el número de filas frente al origen.

## 10. Usuarios de autenticación y archivos

- **Usuarios.** `app_users.external_subject` guarda el identificador que emite el proveedor de identidad. Si el proveedor cambia (Lovable Cloud a Supabase licenciado), ese identificador **cambia** salvo que se importen los usuarios conservando su identificador. Esa importación no está verificada. Mientras no se confirme, la opción segura es volver a aprovisionar cada usuario con `python -m app.cli provision-user --subject <nuevo> --email <correo>` y volver a conceder sus membresías con `grant-membership`. Las membresías no dependen del proveedor, pero sí la vinculación con el usuario.
- **Contraseñas.** No se exportan ni se importan desde este esquema; no existen en él.
- **Archivos.** Este esquema no tiene almacenamiento de archivos. Si en el futuro se guardan archivos (por ejemplo, exportaciones de campañas), se gestionan con el almacenamiento administrado del entorno de destino, no con tablas de esta carpeta.
- **Datos de análisis.** Este esquema todavía no contiene campañas, análisis ni auditoría. Cuando existan, sus migraciones irán en esta carpeta, con su propio archivo y su propia sección en `applied-migrations.md`.

## 11. Campañas y métricas (migración 20261025000200)

Tablas:

- `campaigns`: campaña de una organización. Nombre, canal, objetivo, etiqueta de periodo en texto libre (el analizador acepta texto como "1 al 15 de agosto de 2026"), moneda (`COP` por defecto, formato ISO de tres letras), fechas de periodo opcionales, creador y archivado lógico (`archived_at`).
- `campaign_metric_entries`: registros históricos de métricas. Cada entrada guarda el texto original (`raw_input`, hasta 12.000 caracteres), la fuente (`form` o `text`) y las métricas tal como se introdujeron, más `previous_metrics` (jsonb con objeto, mismo contrato que el analizador).

Invariantes garantizados por PostgreSQL (no solo por la aplicación):

| Regla | Mecanismo |
|---|---|
| Una entrada solo puede apuntar a una campaña de su misma organización | Clave foránea compuesta `(organization_id, campaign_id) -> campaigns(organization_id, id)` |
| El historial no se modifica ni se borra | Disparador `BEFORE UPDATE OR DELETE` que lanza error. Una corrección es una entrada nueva |
| Las métricas no son negativas | `CHECK` por columna en conjunto |
| Fuente, longitud de texto y orden de fechas válidos | `CHECK` |
| Una campaña con historial no se puede borrar | Clave foránea con `ON DELETE RESTRICT`. Para retirar una campaña se usa `archived_at` |
| Moneda con formato ISO | `CHECK` `^[A-Z]{3}$` |

Tipos de datos:

- Inversión e ingresos: `numeric(14,2)`. CPC y CPL (costos unitarios): `numeric(14,4)`. ROAS: `numeric(12,4)`. CTR: `numeric(9,4)`. Frecuencia: `numeric(12,4)`. Nunca coma flotante para dinero.
- Conteos (impresiones, alcance, clics, leads, conversiones): `bigint`. **La API debe rechazar conteos fraccionarios antes de insertar**; la base solo garantiza que no sean negativos.
- Redondeo: al insertar, los valores monetarios se redondean a centavos (medio hacia fuera de cero). Ejemplo verificado: `1234567.005` se guarda como `1234567.01`.
- No hay cota superior para CTR ni para otras métricas: un CTR de 250 % se acepta y el analizador lo marca como advertencia. La base no decide qué dato es correcto.

Pruebas realizadas (base temporal aislada, SQL manual y Alembic con esquemas idénticos): entrada válida aceptada; campaña de otra organización rechazada; organización inexistente rechazada; `UPDATE` y `DELETE` sobre entradas rechazados; valores negativos, fuente inválida, texto de más de 12.000 caracteres, fechas invertidas, `previous_metrics` que no sea objeto, moneda y nombre en blanco, todos rechazados; campaña con historial no borrable; creador inexistente rechazado; RLS activo, el propietario ve las filas y el rol de API ve cero filas.

Notas operativas:
- `TRUNCATE` no está bloqueado por el disparador. Es una operación del propietario, no accesible desde APIs, y la usa la limpieza de pruebas. Si se necesita bloquearlo en producción, debe añadirse en una migración nueva y en Alembic a la vez.
- Los ORM de FastAPI ya describen estas tablas (`backend/app/db/models.py`). Las pruebas `test_campaign_schema.py` verifican estas reglas contra PostgreSQL real.
- Las políticas RLS de estas tablas quedan pendientes hasta validar la identidad (sección 8).

## 12. Verificación de campañas

La sección de campañas de `verification.sql` comprueba: que las dos tablas existen, que el disparador de historial está instalado, que los nombres de restricciones son los esperados, y que RLS está activo sin políticas. Ejecutarla después de la migración 2 y comparar con lo que describen sus comentarios.
