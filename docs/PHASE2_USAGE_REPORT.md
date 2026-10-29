# Fase 2.6B2A: informe mensual de consumo por organización

Estado: implementado en `feat/phase-2-6b2-usage-reports`, apilado sobre `feat/phase-2-6b-provider-usage`. Solo lectura.
No crea tablas ni migraciones, no activa cuotas, no llama a OpenRouter y no incluye dashboards ni auditoría.

## 1. Contrato HTTP

```
GET /api/v1/organizations/{organization_id}/usage?period=YYYY-MM
```

| Situación | Respuesta |
|---|---|
| Sin autenticación | 401 |
| Sin membresía en la organización | 404 `NOT_FOUND` |
| Miembro que no es owner (`analyst`, `client_viewer`) | 403 `FORBIDDEN` |
| Organización suspendida | 403 `FORBIDDEN` |
| `period` distinto de `YYYY-MM` (año 2020–2099, mes 01–12) | 422 |
| `period` omitido | mes UTC actual |
| `ANALYSIS_QUOTA_ENFORCED` con valor inválido | 503 `QUOTA_CONFIGURATION_INVALID` |
| Owner con membresía | 200 `UsageReportRead` |

Solo el rol `owner` (`ADMIN_ROLES`, que ya documentaba «usage reports» en `domain/access.py`). `platform_admin` no existe
en el modelo y no se implementa aquí.

## 2. Respuesta

```json
{
  "organization_id": "…",
  "period": "2026-10",
  "period_start": "2026-10-01",
  "period_end_exclusive": "2026-11-01",
  "timezone": "UTC",
  "has_activity": true,
  "analyses": {
    "requested": 2, "succeeded": 2, "failed": 0, "running": 0,
    "without_quota_record": 0
  },
  "provider_calls": {
    "total": 3, "succeeded": 2, "failed": 1, "reserved": 0, "unknown": 0,
    "tokens": {
      "prompt_total": 812, "completion_total": 430,
      "calls_with_prompt_reported": 1, "calls_with_completion_reported": 1
    },
    "cost": {
      "unit": "openrouter_credits",
      "known_total": "0.00012345",
      "calls_with_cost": 1, "calls_without_cost": 2,
      "explicit_zero_cost_calls": 0,
      "completeness": "partial"
    }
  },
  "quota": {
    "configured": true, "plan": "pilot", "monthly_analysis_limit": 10,
    "enforcement_enabled": false,
    "analyses_counted": 2, "units_consumed": 2, "units_released": 0, "reconciled": true,
    "remaining_available": false, "remaining": null
  }
}
```

`known_total` es texto decimal exacto. Nunca un `float`.

## 3. Reglas de agregación

**Mes de un análisis.** El mes UTC de `analyses.created_at`. Un análisis pertenece a ese mes aunque termine al día siguiente.

**Mes de una llamada.** Una llamada pertenece al mes de **su análisis**, no al de su propia marca temporal. Un análisis que
se crea el 31 de octubre a las 23:59 UTC y registra una llamada el 1 de noviembre cuenta esa llamada en octubre.

**Cambio de mes UTC.** El instante decide: `2030-10-31 23:30` en UTC-5 es `2030-11-01 04:30` UTC y pertenece a noviembre.
Los límites son `[inicio del mes, inicio del mes siguiente)`.

**Análisis y unidades de cuota no son lo mismo.**

- `analyses.requested` cuenta análisis creados en el mes (por `created_at`).
- `quota.analyses_counted` cuenta análisis cuyo `quota_period_start` es el mes. Es el mes en que se reclamó la unidad.
- `quota.units_consumed` es la cuota en vigor (`analysis_quota_usage.consumed`).
- `quota.units_released` son las unidades devueltas: análisis fallidos o abandonados sin ninguna llamada reservada.

**Reconciliación.** `units_consumed + units_released == analyses_counted`. Si no se cumple, `reconciled` es `false` y el
informe lo dice. Es un control de integridad, no un error de entrada.

**Análisis históricos sin cuota.** Los análisis creados antes de la fase 2.6A tienen `quota_period_start` NULL. Entran en
`analyses.requested` y en `analyses.without_quota_record`, pero nunca en `analyses_counted` ni en las unidades. No se
retroactivan.

**Evitar el doble conteo.** Los análisis se cuentan desde `analyses` y las llamadas desde `analysis_usage`. La unión
solo filtra llamadas por el mes de su análisis: nunca se cuenta un análisis a partir de sus filas de llamada. Cada
llamada tiene una sola fila, y cada réplica idempotente no crea filas nuevas.

**Lectura consistente.** Una sola transacción de solo lectura con aislamiento `REPEATABLE READ`. Las cuatro consultas
ven la misma instantánea.

## 4. Datos desconocidos: nunca cero

| Dato | Si falta | Representación |
|---|---|---|
| Tokens de entrada o salida | el proveedor no los reportó en ninguna llamada | `prompt_total` / `completion_total` = `null` |
| Coste | ninguna llamada del mes lo reportó | `known_total` = `null`, `completeness` = `none` |
| Coste | algunas sí, otras no | `known_total` = suma de las conocidas, `completeness` = `partial` |
| Coste | todas lo reportaron | `completeness` = `complete` |
| Coste explícito 0 | el proveedor reportó 0 | cuenta como conocido: `explicit_zero_cost_calls` y suma incluida |
| Mes sin llamadas | — | `completeness` = `no_calls`, todos los totales `null` |
| Cuota | no hay fila de límite | `configured` = `false`, `monthly_analysis_limit` = `null` |
| Remanente | enforcement apagado o sin límite | `remaining` = `null`, `remaining_available` = `false` |

**Indicadores parciales.** Todo total de coste viene con `calls_with_cost` y `calls_without_cost`. Con `partial`, la
suma es un mínimo, no el total. Los tokens siguen el mismo criterio: `calls_with_prompt_reported` indica cuántas llamadas
aportan a la suma.

**Remanente.** Solo se declara si existe un límite y el enforcement está activo. Si no, el campo es `null`, no `0`
ni el límite. Con enforcement apagado, las unidades se siguen contando, pero el remanente no es un hecho.

**Créditos.** La unidad es la que informa OpenRouter (`openrouter_credits`). No se convierte a USD ni a COP. Sumas
con `Decimal` y `NUMERIC(18, 8)`; la serialización es texto.

## 5. Diseño técnico

- `app/domain/usage_report.py`: periodo UTC (`parse_period`), patrón del periodo y regla de completitud
  (`cost_completeness`). Puro, sin base de datos. El periodo por defecto lo calcula PostgreSQL
  (`PHASE2_QUOTA_CLOCK.md`), no Python.
- `app/services/usage_report.py`: `usage_report()` y las consultas agregadas (`_analyses`, `_calls`, `_quota`), más la
  composición de la respuesta. Consultas agregadas en PostgreSQL, sin tabla de resumen.
- `app/api/usage_routes.py`: autorización con `require_role(ADMIN_ROLES)`, validación del periodo, configuración del
  enforcement y llamada al servicio.
- `app/domain/schemas.py`: `UsageReportRead` y sus modelos anidados.

No se creó ninguna tabla materializada: las consultas cubren el volumen actual y el resumen se recalcularía igual desde
las mismas filas. Si el volumen lo exige, la tabla `usage_monthly` del diseño es el paso siguiente, con una migración.

## 6. Pruebas

`backend/tests/test_usage_report.py` (24 pruebas HTTP y de esquema). Cubren owner autorizado, analyst y client_viewer
rechazados, organización ajena, periodo inválido, mes sin actividad, fallback con varias llamadas, reintento de formato,
replays sin duplicados, consumo parcial, costo cero explícito, costo desconocido, cuota desactivada, cuota activada,
cuota sin fila, liberación con reconciliación, registros históricos sin cuota, cambio de mes UTC, análisis que cruza el
mes, aislamiento entre organizaciones, ausencia de texto sensible, precisión decimal, enforcement inválido y contrato
OpenAPI.

## 7. Riesgos pendientes

1. **Volumen (no medido).** Las consultas agregan `analyses` y `analysis_usage` filtrando por organización y mes. Existen
   los índices `ix_analyses_organization_id_created_at` y `ix_analysis_usage_organization_id_created_at`, y la unión
   usa la clave única `(organization_id, id)` de `analyses`. No he comprobado el plan de ejecución con `EXPLAIN` en un
   volumen real. Si hace falta, el paso siguiente es la tabla `usage_monthly` del diseño, con su migración.
2. **Unidad del coste.** Créditos o USD sin confirmar. El informe no convierte nada.
3. **Reloj.** El orden y la pertenencia al mes dependen de `created_at`, que sigue al reloj del sistema (ver
   `PHASE2_PROVIDER_USAGE.md`, sección 8). Un análisis cerca de medianoche UTC puede caer en el mes siguiente si el
   reloj retrocede.
4. **Análisis en ejecución.** `running` cuenta en `requested` y sus llamadas en `reserved`. Un análisis abandonado se
   cierra cuando vence su lease; hasta entonces el informe lo muestra como en ejecución.
5. **Platform admin.** No existe. Cuando se diseñe, necesitará su propia autorización, no ampliar `ADMIN_ROLES` a ciegas.
6. **Sin auditoría.** No hay `audit_events`: quién consultó el informe no queda registrado.
