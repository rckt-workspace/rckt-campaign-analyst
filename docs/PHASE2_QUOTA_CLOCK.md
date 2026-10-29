# Fase 2.7A: consistencia temporal de las cuotas

Estado: implementada en `feat/phase-2-7a-quota-clock`, apilada sobre `feat/phase-2-6b2-admin-ui`. Sin migraciones, sin
tablas nuevas, sin cambios de contrato HTTP y sin cambios en el frontend.

## 1. Problema

Un análisis guardaba dos marcas de tiempo distintas. `created_at` lo fijaba PostgreSQL dentro de la transacción de
reclamación. El mes de cuota (`analyses.quota_period_start`) se calculaba antes, en Python, con el reloj de la aplicación
(`quota_period_for(datetime.now(UTC))`). Si la reclamación caía justo en el cambio de mes, el análisis podía tener
`created_at` en octubre y una unidad imputada a noviembre, o al revés. El informe mensual agrupa por `created_at`, así que
el consumo mostrado y el consumo contado podían diferir.

## 2. Referencia única: el reloj de PostgreSQL

Todo el cálculo del mes ocurre dentro de la transacción que reclama el análisis:

- **Instante.** `now()` de PostgreSQL es el inicio de la transacción. Todas las sentencias de esa transacción ven el mismo
  valor. `created_at` se fija con `now()` y el mes se calcula con `now()` en la misma transacción, así que no pueden
  discrepar.
- **Mes UTC.** `app/db/clock.py` define `utc_quota_period(instante)`:
  `CAST(date_trunc('month', timezone('UTC', instante)) AS date)`. `timezone('UTC', timestamptz)` devuelve la hora de pared
  en UTC sin depender de `TimeZone` de la sesión ni del servidor. El resultado es el día 1 del mes UTC.
- **Orden dentro de `_claim`** (`app/db/analysis_ledger.py`): primero se lee el mes, después se asegura la fila de
  `analysis_quota_usage` del mes, después se inserta el análisis con `created_at = now()` y
  `quota_period_start = mes`, y por último se consume la unidad. Si algo falla, la transacción entera se revierte.
- **Python no interviene en el mes.** `analysis_ledger`, `analysis_history`, `usage_routes` y `clock` no llaman a
  `datetime.now`, `utcnow`, `date.today` ni `time.time`. Esto lo comprueba una prueba estática.
- **Informe por defecto.** Si `GET /api/v1/organizations/{id}/usage` no recibe `period`, el mes lo calcula la misma expresión
  SQL. El parámetro `period=YYYY-MM` sigue igual.

## 3. Por qué no leer el mes antes de la reclamación

Leer el mes en una transacción previa abriría una ventana: el instante leído podría no ser el que PostgreSQL asigna después
a `created_at`, y el límite de mes seguiría siendo incoherente. Por eso el mes se lee dentro de la transacción que
reclama, con el mismo `now()` que usa `created_at`.

## 4. Replays y errores

- Un replay (misma clave de idempotencia) devuelve el análisis original. Su `quota_period_start` no cambia y no consume
  ninguna unidad. Las ramas `claimed` y `existing` no se modificaron.
- Si la reclamación falla después de leer el mes (por ejemplo, al consumir la unidad), la transacción se revierte: no queda
  análisis, ni unidad, ni evento de auditoría.
- El modo sin enforcement sigue contando la unidad del mes actual y nunca rechaza.

## 5. Límites conocidos

- `now()` depende del reloj del servidor PostgreSQL. PostgreSQL no garantiza monotonía absoluta de `now()` entre
  transacciones: si ese reloj retrocede, `created_at` y el mes de un análisis posterior pueden quedar por detrás de
  los de uno anterior. La coherencia se garantiza dentro de cada transacción, no entre ellas. Esta fase no cambia esa
  fuente: la verdad temporal es la base de datos. El retroceso medido (~0,5 s) está documentado en
  `PHASE2_PROVIDER_USAGE.md` §8.
- Los análisis anteriores a la fase 2.6A no tienen `quota_period_start`. No cambian.
- `claim_analysis_in_period` (periodo fijo) existe solo para pruebas. Ninguna ruta de producción la llama.
- `current_period(instant)` de `app/domain/usage_report.py` ya no lo usa el camino de cuota. Solo la prueba de informe lo
  usa para calcular el valor esperado.
- El selector de periodo del frontend usa la fecha del navegador. Es solo presentación; el backend decide el mes.

## 6. Pendientes fuera de esta fase

- Ejecución E2E completa en un solo proceso y revisión visual de `/administracion`: pendientes desde la fase 2.6B2C.
  Esta fase no las ejecuta ni las sustituye.

## 7. Pruebas

Comandos: `pytest tests/test_quota_clock.py tests/test_quota.py tests/test_analysis_ledger.py` (75 pasan) y la suite completa
`pytest tests` en un solo proceso, secuencial, sin E2E (451 pasan). Base de pruebas: `rckt_test`.

| Requisito | Prueba (`backend/tests/test_quota_clock.py`) |
|---|---|
| 1. Periodo correcto en una reclamación nueva | `test_the_month_is_read_from_postgresql_not_from_the_application` |
| 2. Mes calculado por PostgreSQL | `test_the_period_is_the_utc_month_at_every_boundary` (expresión SQL sobre instantes elegidos) |
| 3. Independencia del reloj local de Python | `test_no_module_on_the_month_path_reads_the_python_clock` (comprobación estática del código de la ruta) |
| 4. Independencia de la zona horaria de la sesión | `test_the_session_time_zone_does_not_move_the_utc_month` |
| 5. Límite de mes UTC | `test_the_period_is_the_utc_month_at_every_boundary` (23:59:59,999999 UTC frente a 00:00 UTC) |
| 6. Diciembre a enero | mismo test: `2030-12-31T23:30-05:00` produce `2031-01-01` |
| 7. Coherencia `created_at` y `quota_period_start` | `test_the_claim_month_and_created_at_describe_the_same_instant` |
| 8. Replay sin consumo adicional | `test_a_replay_keeps_the_original_month_and_consumes_nothing` |
| 9. Concurrencia con la última unidad | `test_two_concurrent_claims_on_the_database_clock_admit_exactly_one` |
| 10. Cuota agotada sin llamada al proveedor | `test_an_exhausted_quota_on_the_database_month_answers_429_without_a_provider_call` |
| 11. Enforcement desactivado | `test_with_enforcement_off_the_unit_is_counted_in_the_database_month` |
| 12. Rollback sin análisis ni unidad | `test_a_failure_after_the_month_is_read_rolls_back_the_analysis_and_the_unit` |
| 13. Compatibilidad con informe y auditoría | `test_the_audit_event_and_the_monthly_report_carry_the_database_month` |

Las pruebas de límite no mueven el reloj de PostgreSQL: evalúan la misma expresión que usa la reclamación sobre instantes
fijados en la prueba. La comprobación de que el mes es UTC y no la zona de la sesión se hace comparando con la lectura sin
conversión (`naive_month_in_zone`), que sí cambia de mes en `Pacific/Kiritimati`.

`backend/tests/test_quota.py` tiene 36 pruebas. Las pruebas de política de periodo con Python se retiraron; las pruebas de
consumo usan un mes fijo por `claim_analysis_in_period`, y las que dependen del mes actual leen `db_period`, que pregunta a
PostgreSQL.
