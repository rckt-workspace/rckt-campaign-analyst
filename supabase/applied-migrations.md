# Registro manual de migraciones SQL aplicadas

Actualizar esta tabla **después** de ejecutar cada archivo, con quien lo ejecutó y el resultado de `verification.sql`.
No editar historiales internos de Alembic, Drizzle ni Supabase CLI para corregir este registro.

| Archivo | Entorno (nombre, no identificador de proyecto) | Fecha | Ejecutado por | Resultado de `verification.sql` | Notas |
|---|---|---|---|---|---|
| `migrations/20261025000100_foundation.sql` | Desarrollo local (`rckt`) | — | — | — | Equivalente a Alembic `0001` + `0002`. Aplicado con Alembic, no con este archivo. |
| `migrations/20261025000100_foundation.sql` | Lovable Cloud | sin registrar (informado por el responsable como ejecutado correctamente) | responsable de RCKT | pendiente de registrar | Ejecutar una sola vez. |
| `migrations/20261025000100_foundation.sql` | Supabase licenciado | pendiente | pendiente | pendiente | Futuro. |
| `migrations/20261025000200_campaigns.sql` | Desarrollo local (`rckt`) | — | — | — | Aplicado con Alembic `0003_campaigns`, no con este archivo. |
| `migrations/20261025000200_campaigns.sql` | Lovable Cloud | pendiente | pendiente | pendiente | Requiere la fundación ya aplicada. Ejecutar una sola vez. |
| `migrations/20261025000200_campaigns.sql` | Supabase licenciado | pendiente | pendiente | pendiente | Futuro. |
