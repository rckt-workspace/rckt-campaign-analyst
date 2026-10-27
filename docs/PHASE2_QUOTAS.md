# Fase 2.6A: cuotas de análisis por organización

Estado: **implementada en la rama `feat/phase-2-6-quotas-audit`, desactivada por defecto**. No se aplica en Lovable
Cloud ni en la base local de desarrollo `rckt`. No hay facturación ni límites monetarios. No hay llamadas reales a
OpenRouter en las pruebas.

## 1. Qué se limita y qué no

| Límite | Estado | Motivo |
|---|---|---|
| **Número de análisis por organización y mes UTC** | Implementado | Se puede contar sin conocer el coste de cada llamada. |
| Coste monetario por organización | **No implementado** | Requiere una captura verificada del `usage` de OpenRouter y un peor coste por llamada. Ver sección 6. |
| Planes comerciales (`pilot`, `standard`, `enterprise`) | Solo etiqueta, sin límites propios | No se inventan límites. El operador define el número por organización. |

## 2. Política de consumo (determinista)

Una solicitud de análisis **consume una unidad** de la cuota del mes UTC en que se reclama. La unidad se decide por el
resultado, según si alguna llamada llegó a reservarse:

| Resultado | Llamadas reservadas | Unidad |
|---|---|---|
| Éxito (`succeeded`) | ≥ 1 | Se mantiene consumida |
| Fallo tras llamar al proveedor (`failed`) | ≥ 1 | Se mantiene consumida: el coste puede existir |
| Abandono por lease vencido (`failed`, `ANALYSIS_ABANDONED`) | ≥ 1 | Se mantiene consumida: el coste es desconocido |
| Fallo sin llamadas (`failed`, p. ej. `AI_NOT_CONFIGURED`) | 0 | **Se libera** |
| Abandono por lease vencido sin llamadas | 0 | **Se libera** |
| Repetición con la misma clave (replay) | — | No consume nada |
| Clave reutilizada con otra entrada (422) | — | No consume nada |
| Cuota agotada (429) | — | No se crea análisis, no consume nada |

La regla «sin llamadas reservadas ⇒ no hubo llamada» es válida porque **toda llamada se reserva antes**, en una transacción
confirmada (ver `docs/PHASE2_ANALYSES.md`, sección 4). Un análisis sin ninguna fila en `analysis_usage` nunca llamó al proveedor.

Los reintentos de formato y el fallback **no consumen unidades adicionales**: la unidad es por análisis.

- Dos peticiones simultáneas con la misma clave: una reclama y la otra recibe `existing`; se consume una sola unidad.
- Un replay en un mes nuevo no consume la unidad de ese mes. La fila del mes puede crearse vacía (0, 0); es inofensiva.

## 3. Modelo de datos (migración `0006_analysis_quotas`)

- **`analysis_quotas`**: una fila por organización con `plan` (`pilot`, `standard`, `enterprise`) y
  `monthly_analysis_limit` (entero ≥ 0). Sin fila, no hay límite configurado.
- **`analysis_quota_usage`**: una fila por organización y mes (`period_start` = día 1 UTC).
  `consumed` (unidades vigentes) y `released` (unidades devueltas, para auditoría). Clave primaria `(organization_id, period_start)`.
- **`analyses.quota_period_start`**: mes UTC al que se imputó la unidad. Nulo para análisis anteriores a esta fase, que no se
  imputan ni se retroactivan. Clave foránea compuesta a `analysis_quota_usage`.

Todas las tablas tienen `organization_id` y claves compuestas con él: no hay cruce entre organizaciones.
RLS activo sin políticas, sin GRANT a `anon` ni `authenticated`, igual que el resto del esquema.

## 4. Atomicidad y concurrencia

- La reserva **va en la misma transacción** que la reclamación del análisis. Si la reserva falla, la reclamación se revierte:
  no queda ni análisis ni unidad consumida.
- La reserva es **una sola sentencia condicional**:

  ```sql
  UPDATE analysis_quota_usage u SET consumed = u.consumed + 1
  FROM analysis_quotas q
  WHERE u.organization_id = q.organization_id
    AND u.organization_id = :org AND u.period_start = :period
    AND u.consumed < q.monthly_analysis_limit;
  ```

  Si afecta 0 filas, la cuota está agotada. La fila de contador queda bloqueada durante la sentencia: dos solicitudes simultáneas
  con una sola unidad libre no pueden pasar ambas. La segunda espera al commit de la primera y reevalúa la condición.
- Orden dentro de la transacción de reclamación (`backend/app/db/analysis_ledger.py`):
  1. `INSERT … ON CONFLICT DO NOTHING` de la fila del mes, para que exista la clave foránea del análisis.
  2. Inserción del análisis con `ON CONFLICT DO NOTHING` sobre la clave de idempotencia.
  3. Solo si la inserción fue nueva, la reserva condicional. Si la cuota la rechaza, la transacción entera se revierte:
     no queda ni análisis ni unidad. Si la clave ya existía, el contador no se toca.
- La liberación (`consumed − 1`, `released + 1`) ocurre en la misma transacción que el cierre `failed` o el abandono, y
  solo si no hay ninguna fila en `analysis_usage` para ese análisis. Esa transacción bloquea antes la fila del análisis
  (`FOR UPDATE`), así que no puede reservarse una llamada entre el recuento y la liberación.

## 5. Interruptor de activación

`ANALYSIS_QUOTA_ENFORCED` (por defecto `false`):

- **`false`**: el sistema cuenta las unidades (la contabilidad siempre corre) pero no rechaza nada. Así, al activar más tarde, el
  historial de consumo ya existe.
- **`true`**: la reserva rechaza cuando no hay unidades (**429 `QUOTA_EXCEEDED`**) o cuando falta la fila de cuota (**503 `QUOTA_NOT_CONFIGURED`**, error de configuración del operador, no de permisos).
  El endpoint legado `POST …/campaigns/analyze` está **retirado de forma permanente** desde la fase 2.6A: responde siempre **410 `ENDPOINT_RETIRED`**, sin autenticación ni llamada al proveedor. Ningún consumidor lo usa (búsqueda en `src`, `e2e` y `backend/app`); sus pruebas de autorización migraron al endpoint persistente.
- Si `ANALYSIS_QUOTA_ENFORCED` no es un valor reconocible, la creación responde **503 `QUOTA_CONFIGURATION_INVALID`** y no
  reclama nada (falla cerrada). Valores verdaderos: `1`, `true`, `yes`, `on`. Falsos: vacío, `0`, `false`, `no`, `off`.

**Resuelto:** el endpoint legado ya no puede llamar al proveedor en ningún modo, así que no queda ninguna vía sin contar.

Justificación del valor por defecto: sin límites comerciales aprobados, activar la restricción bloquearía a todas las
organizaciones sin fila de cuota. Activarla requiere decidir los límites y asignarlos con el CLI antes.

## 6. Qué falta para límites monetarios estrictos (Fase 2.6B)

1. Captura verificada del campo `usage` de OpenRouter (tokens de entrada y salida, y coste si lo informa). Hoy el transporte no lo lee.
2. Peor coste por llamada: tokens máximos (`CHAT_MAX_TOKENS`) multiplicados por el precio de entrada y salida del modelo.
   Sin esa cifra no hay reserva monetaria segura, porque cada análisis puede hacer hasta cuatro llamadas.
3. Política de reserva monetaria: reservar el peor coste antes de la llamada y ajustarlo con el coste real después.
4. Moneda y redondeo definidos. Por ahora no se convierte nada y no hay ninguna cifra en pesos ni en dólares.

Mientras tanto, cualquier coste no reportado se guarda como `NULL` y nunca como cero.

## 7. Operación

```
python -m app.cli set-analysis-quota --organization-slug cliente-uno --plan pilot --monthly-limit 50
```

Crea o actualiza la fila de cuota. Requiere `DATABASE_URL` o `DATABASE_MIGRATION_URL`, como el resto de procedimientos del operador.

## 8. Verificación

`backend/tests/test_quota.py`: 38 pruebas. Cubren la política de periodo, el flag estricto, el consumo, el agotamiento,
dos reclamaciones concurrentes con una unidad, el replay, el aislamiento por organización, el cambio de mes UTC, la cuota
ausente, el modo sin enforcement, un fallo de BD durante la reserva, la liberación con y sin llamadas, el reintento de
formato, el fallback, el endpoint legado y el CLI. Ningún test llama a OpenRouter real.

Comprobación SQL↔Alembic: una base aislada construida con los archivos SQL 0001–0005 se comparó con `rckt_test` (construida
por Alembic) mediante `pg_dump -s`. Sin diferencias salvo la tabla `alembic_version` (propia de Alembic) y las líneas
`
estrict` aleatorias de pg_dump. Las funciones de los triggers solo difieren en indentación.

Frontend: un 429 muestra el aviso `QUOTA_EXCEEDED`. Un 503 (cuota no configurada o configuración de enforcement inválida)
se trata como indisponibilidad del servicio y no revela detalles internos. Un 403 vuelve a significar solo permisos.

Pendiente antes de activar en producción: decidir los límites por organización, comprobar la migración en una base aislada
con la estructura de Lovable, y verificar los privilegios de `anon` y `authenticated` sobre las tablas nuevas (ver `docs/PHASE2_ANALYSES.md`, sección 12).
