# Fase 2.1: fundación de persistencia

Esta fase crea la infraestructura de datos y el esquema mínimo. No implementa usuarios con sesión, campañas, análisis, cuotas ni auditoría; esas funciones siguen el orden del diseño en `PHASE2_DESIGN.md`.

## Qué incluye

- PostgreSQL en desarrollo, como servicio de Compose en red privada, con volumen persistente, healthcheck y sin puerto publicado.
- SQLAlchemy 2 asíncrono con el driver `asyncpg`, y Alembic para migraciones versionadas.
- Esquema mínimo: `organizations`, `app_users` y `organization_memberships`.
- Autorización por organización desde la primera operación: `require_role` y los roles `owner`, `analyst` y `client_viewer`.
- `/readyz` que verifica la base de datos. `/healthz` sigue sin dependencias.
- La aplicación no crea tablas al arrancar. El esquema solo cambia con migraciones.

## Estructura

```
backend/
  app/
    core/config.py          configuración de conexión (DATABASE_URL y parámetros del pool)
    db/
      base.py               Base declarativa, nombres de restricciones explícitos
      models.py             modelos de persistencia (solo tablas, sin reglas de negocio)
      session.py            motor async, ciclo de vida de sesiones, errores controlados
      repositories.py       acceso a datos; las consultas de organización exigen organization_id
    domain/access.py        roles y reglas (quién puede generar costo de IA)
    services/access.py      require_role: membresía y rol antes de cualquier operación
  migrations/
    env.py                  entorno Alembic asíncrono; la URL viene del entorno, nunca de un archivo
    versions/20261024_0001_foundation.py
  tests/                    pruebas de base de datos (requieren TEST_DATABASE_URL) y de configuración
db/init/01-create-test-database.sql   crea rckt_test en el primer arranque del volumen
compose.dev.yml                       db, migrate, tests (solo desarrollo; se combina explícitamente)
```

Separación de responsabilidades: los modelos describen tablas, las reglas de rol viven en `domain/`, y el acceso a datos está en repositorios. Los esquemas de la API siguen en `domain/schemas.py`, sin cambios.

## Decisiones de esquema

**Identificadores.** Todas las claves primarias son `uuid`. Se generan en la aplicación (`uuid4`) y tienen un valor por defecto en la base (`gen_random_uuid()`, integrado desde PostgreSQL 13). Así una fila insertada a mano también recibe identificador. Los UUID no revelan el volumen de registros ni son adivinables.

**Zonas horarias.** Todas las marcas de tiempo son `timestamptz` con `DEFAULT now()`. La base las guarda en UTC, así que el servidor de aplicación y el cliente no afectan los valores. `updated_at` se actualiza desde la aplicación con `onupdate`.

**Claves foráneas.** `organization_memberships` referencia `organizations` y `app_users` con `ON DELETE RESTRICT`. Borrar una organización o un usuario falla mientras tenga membresías, en vez de borrar en cascada el historial de acceso. Las eliminaciones reales se diseñarán con retención en la fase de cuotas y auditoría.

**Índices y restricciones.**
- `organizations.slug`: único, y debe cumplir `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`. El slug forma parte de URLs y de la identificación de clientes.
- `organizations.status`: `active` o `suspended`.
- `app_users.external_subject`: único. Es el identificador estable que entrega el proveedor de autenticación. No se guardan contraseñas.
- `organization_memberships`: único por `(organization_id, user_id)`, rol restringido a `owner`, `analyst` o `client_viewer`, e índice en `user_id` para listar las organizaciones de un usuario.

Los nombres de restricciones son explícitos en modelos y migración. Así Alembic compara ambos sin falsas diferencias.

**Correos.** `app_users.email` no es único. La identidad es `external_subject`. Un correo puede cambiar en el proveedor; no debe ser la clave.

## Migraciones: procedimiento

Desarrollo (Compose; siempre con los dos archivos explícitos):

```bash
docker compose -f docker-compose.yml -f compose.dev.yml up -d db              # PostgreSQL con healthcheck
docker compose -f docker-compose.yml -f compose.dev.yml run --rm migrate      # ejecuta: alembic upgrade head
```

El servicio `migrate` (en `compose.dev.yml`) también se ejecuta antes de `backend`, porque `backend` depende de que termine correctamente.

Producción (PostgreSQL gestionado): no se usa el archivo de desarrollo. Las migraciones se ejecutan una vez por despliegue, con un rol de migración distinto del rol de la aplicación:

```bash
DATABASE_MIGRATION_URL=postgresql+asyncpg://rckt_migrator:...@host:5432/rckt \
  alembic upgrade head                # desde backend/, o como comando de un contenedor de un solo uso
```

Reglas:
- Cada cambio de esquema es una migración nueva. No se editan migraciones ya aplicadas.
- Antes de desplegar, se verifica en CI que `models.py` coincide con las migraciones (`test_models_match_migrated_schema`).
- Antes de llegar a producción, cada migración se prueba hacia arriba y hacia abajo (`test_downgrade_and_upgrade_round_trip`).
- Las URL de conexión nunca van en `alembic.ini` ni en el repositorio.

## Configuración

| Variable | Obligatoria | Uso |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Solo desarrollo | Contenedor `db`. `POSTGRES_PASSWORD` no tiene valor por defecto. |
| `DATABASE_URL` | No | `postgresql+asyncpg://...`. Si falta, la aplicación arranca sin persistencia, como en la Fase 1. |
| `DATABASE_MIGRATION_URL` | No | URL del rol de migración. Si falta, se usa `DATABASE_URL`. |
| `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW` | No | Tamaño del pool (5 y 5 por defecto). |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | No | Tiempo máximo para conectar (5 s por defecto). |
| `TEST_DATABASE_URL` | Para pruebas de BD | Base de pruebas. El nombre debe terminar en `_test`; las pruebas se niegan a ejecutarse en cualquier otra base. |

## Manejo de errores y seguridad

- Las sesiones se abren por petición mediante `Database.session()`. Hacen commit al terminar bien, rollback ante cualquier excepción, y siempre se cierran.
- Los errores de conexión de bajo nivel (`OperationalError`, `OSError`) se convierten en `DatabaseUnavailableError` con el mensaje fijo `database unavailable`. El mensaje del driver puede incluir host y puerto, así que nunca se propaga ni se registra.
- `/readyz` responde 503 sin detalles cuando la base no está disponible o no está configurada.
- Una URL con esquema incorrecto produce `DatabaseConfigurationError` sin mostrar la URL ni la contraseña.
- El rol de la aplicación debe tener solo `SELECT`, `INSERT`, `UPDATE` y `DELETE` sobre sus tablas. El rol de migración es el único con DDL.

## Compatibilidad con la Fase 1

- Sin `DATABASE_URL`, el backend arranca igual que antes y `/healthz` no cambia.
- `POST /api/v1/campaigns/analyze` y el frontend no cambiaron de contrato ni de código.
- Las pruebas existentes de backend y las 8 pruebas E2E se conservan como línea base.

## Pruebas

Las pruebas se ejecutan en Python 3.12 dentro de Compose contra PostgreSQL real:

```bash
docker compose -f docker-compose.yml -f compose.dev.yml up -d db
docker compose -f docker-compose.yml -f compose.dev.yml --profile test run --rm tests
```

El servicio `tests` monta `backend/` como solo lectura y usa la base `rckt_test`. Cubre migraciones (creación, coincidencia con los modelos y ida y vuelta de downgrade/upgrade), restricciones de la base (slug, estado, rol, unicidad de membresía), aislamiento entre organizaciones, rol `client_viewer` sin costo de IA, rollback de transacciones fallidas, persistencia entre conexiones, y errores de conexión sin filtrar secretos.

Sin `TEST_DATABASE_URL`, las pruebas de base de datos se omiten con un motivo explícito; las demás se ejecutan igual.

## Limitaciones de esta fase

- No hay usuarios con sesión ni integración con el proveedor de autenticación. `require_role` está listo para usarse, pero ningún endpoint lo llama todavía.
- No hay campañas, análisis guardados, cuotas ni auditoría (fases 2.4 a 2.6).
- Row Level Security de PostgreSQL no está activado (etapa 2.7).
- Las copias de seguridad dependen del proveedor de PostgreSQL gestionado; la política aún no está definida.
