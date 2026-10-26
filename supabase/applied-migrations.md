# Registro manual de migraciones SQL aplicadas

Actualizar esta tabla **después** de ejecutar cada archivo, con quien lo ejecutó y el resultado de `verification.sql`.
No editar historiales internos de Alembic, Drizzle ni Supabase CLI para corregir este registro.

| Archivo | Entorno (nombre, no identificador de proyecto) | Fecha | Ejecutado por | Resultado de `verification.sql` | Notas |
|---|---|---|---|---|---|
| `migrations/20261025000100_foundation.sql` | Desarrollo local (`rckt`) | — | — | — | Equivalente a Alembic `0001` + `0002`. Aplicado con Alembic, no con este archivo. |
| `migrations/20261025000100_foundation.sql` | Lovable Cloud | sin fecha registrada | responsable de RCKT (aplicada manualmente en el SQL Editor) | Verificada mediante consultas SQL; el responsable confirmó tablas, restricciones y RLS activo | Ejecutada una sola vez. No volver a ejecutar. |
| `migrations/20261025000100_foundation.sql` | Supabase licenciado | pendiente | pendiente | pendiente | Futuro. |
| `migrations/20261025000200_campaigns.sql` | Desarrollo local (`rckt`) | — | — | — | Aplicado con Alembic `0003_campaigns`, no con este archivo. |
| `migrations/20261025000200_campaigns.sql` | Lovable Cloud | sin fecha registrada | responsable de RCKT (aplicada manualmente en el SQL Editor) | Verificada mediante consultas SQL según informó el responsable | Ejecutada una sola vez, después de la fundación. No volver a ejecutar. |
| `migrations/20261025000200_campaigns.sql` | Supabase licenciado | pendiente | pendiente | pendiente | Futuro. |
| `migrations/20261025000300_analyses.sql` (Alembic `0004_analyses`) | Desarrollo local (`rckt`) | 2026-10-26 | Servicio `migrate` de Compose (`alembic upgrade head`) | Consulta de solo lectura: `alembic_version` = `0005_format_gate`; estructura comparada con la base de prueba: sin diferencias | Aplicada localmente. No aplicada en Lovable Cloud. |
| `migrations/20261025000400_format_gate.sql` (Alembic `0005_format_gate`) | Desarrollo local (`rckt`) | 2026-10-26 | Servicio `migrate` de Compose (`alembic upgrade head`) | Consulta de solo lectura: `alembic_version` = `0005_format_gate`; estructura comparada con la base de prueba: sin diferencias | Aplicada localmente. No aplicada en Lovable Cloud. |
| `migrations/20261025000400_format_gate.sql` (Alembic `0005_format_gate`) | Lovable Cloud | pendiente | pendiente de aprobación del responsable de RCKT | pendiente | Pendiente de revisión. No ejecutar hasta revisar los privilegios de `anon` y `authenticated` sobre las tablas de análisis (ver `docs/PHASE2_ANALYSES.md`). |
| `migrations/20261025000400_format_gate.sql` (Alembic `0005_format_gate`) | Supabase licenciado | pendiente | pendiente | pendiente | Futuro. |
| `migrations/20261025000300_analyses.sql` (Alembic `0004_analyses`) | Lovable Cloud | pendiente | pendiente de aprobación del responsable de RCKT | pendiente | Pendiente de revisión. No ejecutar hasta revisar los privilegios de `anon` y `authenticated` sobre las tablas de análisis. |
| `migrations/20261025000300_analyses.sql` | Supabase licenciado | pendiente | pendiente | pendiente | Futuro. |

## Nota sobre el registro

- Estas filas registran ejecuciones **manuales**. No son un historial automático: ni Supabase CLI (`supabase_migrations.schema_migrations`) ni Drizzle ni Alembic conocen estas aplicaciones. Si en el futuro se usa el CLI sobre la misma base, puede volver a intentar aplicar estos archivos; ver `supabase/README.md`, sección 4.
- No editar historiales internos para "corregir" este registro. Actualizar solo este archivo.
- Las fechas no se registraron en el momento de la ejecución. Completarlas cuando se tengan.
