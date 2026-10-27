# Fase 2.6B1: consumo reportado por OpenRouter

Estado: implementada en la rama `feat/phase-2-6b-provider-usage`, apilada sobre `feat/phase-2-6-quotas-audit`. No activa
cuotas monetarias, no llama a OpenRouter real y no aplica migraciones a `rckt` ni a Lovable Cloud.

## 1. Contrato real de OpenRouter (verificado)

Fuentes consultadas el 2026-10-27:

- Usage accounting: https://openrouter.ai/docs/use-cases/usage-accounting
- Referencia de la API de chat completions: https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request

Hechos confirmados:

- Una respuesta **no streaming** incluye un objeto `usage`. No hace falta ningún parámetro para recibirlo.
- Campos de `usage` usados: `prompt_tokens` (entero), `completion_tokens` (entero), `total_tokens` (entero) y `cost`
  (número, o `null`). `cost` es el importe total cargado a la cuenta.
- El parámetro `usage: {include: true}` y `stream_options.include_usage` están **obsoletos y no tienen efecto**. No se envían.
- `cost_details.upstream_inference_cost` solo está disponible para solicitudes BYOK o consultando por ID de generación. No se usa.
- No hay ninguna petición adicional para leer costes: todo viene en la misma respuesta.

Hechos **no confirmados**, y por tanto tratados como desconocidos:

- **Unidad de `cost`**. La página de usage accounting describe `cost` como coste «en créditos»; la referencia de la API
  lo tipa como número sin unidad. No hay una fuente que confirme la equivalencia 1 crédito = 1 USD. No se asume USD.
  Por eso `cost_micros` (micro-USD) no se rellena todavía: el valor exacto se guarda en `provider_cost`.
- Si `cost` aparece siempre o solo en algunos casos (p. ej., modelos gratuitos). No se supone ninguna de las dos cosas.

## 2. Reglas de datos

| Situación | Almacenado |
|---|---|
| No hay objeto `usage` | `usage_reported = false`; tokens y `provider_cost` en NULL |
| Solo tokens | tokens guardados; `provider_cost` NULL |
| Solo `cost` | `provider_cost` guardado; tokens NULL |
| `cost: 0` explícito | `provider_cost = 0` (cero reportado, no ausente) |
| Campo mal formado (cadena, booleano, decimal en un contador, negativo, NaN, fuera de rango) | ese campo NULL; los demás se conservan; se registra solo el nombre del campo |
| Solo campos inválidos | como «no hay objeto usage» |
| Timeout | llamada `failed` o `unknown` con consumo NULL: el resultado es desconocido |
| Respuesta vacía que sí cobró (`AI_EMPTY` con `usage`) | consumo guardado en la llamada **fallida** |
| Respuesta correcta sin `usage` | análisis válido; el intento queda sin consumo, igual que una llamada sin datos |

La falta de `usage` o de un campo **nunca rechaza una respuesta correcta**. No hay ningún caso en el que un consumo
inválido cambie el texto del análisis.

No se interpreta NULL como cero. Un coste cero solo se guarda cuando el proveedor lo informa explícitamente.

## 3. Arquitectura de medición

```
call_model_with_usage  ->  parse_usage (Decimal, reglas de §2)  ->  ModelAnswer(text, usage)
        |                                                                  |
        |  _observed_call: finished(token, usage=...) una vez por llamada  |
        v                                                                  v
_StepObserver (analysis_history) ->  ledger.complete_attempt  ->  analysis_usage (una fila por llamada HTTP)
```

- **Transporte** (`app/integrations/openrouter.py`): el JSON se lee con `parse_float=Decimal`, de forma que el coste se conserva
  exactamente como lo escribió el proveedor. `parse_usage` aplica las reglas de §2.
- **Dominio** (`app/domain/usage.py`): `ProviderUsage`, límites de columna y `usd_to_micros`. Sin E/S.
- **Registro** (`app/services/analysis_history.py`): cada llamada HTTP tiene su propio token y su propia fila. El consumo
  de una llamada nunca se suma ni se reparte con otra.
- **Ledger** (`app/db/analysis_ledger.py`): `complete_attempt` escribe solo los valores reportados y marca
  `usage_reported` si hay al menos uno.

## 4. Conversión monetaria

- Toda cantidad es `Decimal`. Ningún `float` entra en un cálculo monetario; `usd_to_micros` rechaza floats.
- `provider_cost` se redondea **medio hacia arriba a 8 decimales** (la escala de `NUMERIC(18, 8)`).
- `usd_to_micros(amount)` multiplica por 1 000 000 y redondea **medio hacia arriba** a un micro entero:
  `0.0000005 -> 1`, `0.0000004 -> 0`. Rechaza negativos, no finitos y fuera de rango.
- El rango admitido de `provider_cost` es `[0, 9999999999.99999999]`, que es el máximo de la columna.
- La función existe y está probada, pero **no se aplica todavía**: falta confirmar la unidad de `cost` (§1).

## 5. Migración 0007

`supabase/migrations/20261027000600_provider_cost.sql` (espejo Alembic `0007_provider_cost`):

- Añade `analysis_usage.provider_cost numeric(18, 8)`, nullable.
- `ck_analysis_usage_provider_cost_non_negative`: el coste no puede ser negativo.
- `ck_analysis_usage_provider_cost_reported`: un coste solo existe si `usage_reported` es verdadero.

Aditiva. No modifica 0001–0006. Pendiente en local `rckt`, Lovable Cloud y Supabase licenciado.

## 6. Ejemplos de registros (simulados)

Análisis con primario fallido y fallback correcto, con reintento de formato en el fallback:

| attempt | step | role | status | error | usage_reported | prompt_tokens | completion_tokens | provider_cost |
|---|---|---|---|---|---|---|---|---|
| 1 | initial | primary | failed | AI_HTTP_ERROR (503) | false | NULL | NULL | NULL |
| 2 | initial | fallback | succeeded | NULL | true | 812 | 430 | 0.00012345 |

Reintento de formato con coste explícito cero en el segundo intento:

| attempt | step | role | status | usage_reported | provider_cost |
|---|---|---|---|---|---|
| 1 | initial | primary | succeeded | true | 0.00100000 |
| 2 | format_retry | primary | succeeded | true | 0.00000000 |

Timeout con resultado desconocido (abandono de lease):

| attempt | status | error | usage_reported | provider_cost |
|---|---|---|---|---|
| 1 | unknown | ANALYSIS_ABANDONED | false | NULL |

## 7. Casos en los que el consumo permanece desconocido

- Timeout o desconexión: el proveedor puede haber cobrado; no hay respuesta que lo confirme.
- Abandono por lease vencido con una llamada reservada: resultado desconocido.
- Campo mal formado: NULL en ese campo.
- Proveedor que no informa `usage`: NULL en todo el intento.
- Coste en una unidad no confirmada: se guarda el valor exacto, pero no se convierte a micro-USD.

## 8. Reloj: causa verificada y corrección

**Síntoma.** Con llamadas secuenciales, `completed_at` salía anterior a `created_at` y el CHECK
`ck_analysis_usage_completed_after_created` rechazaba la completación. Como consecuencia, un análisis correcto se cerraba
como `ANALYSIS_ABANDONED` o quedaba con un intento reservado.

**Qué significan los relojes de PostgreSQL.**

- `now()` es `transaction_timestamp()`: el instante en que empezó la transacción actual. Es igual en todas las
  sentencias de esa transacción.
- `statement_timestamp()` es el instante en que empezó la sentencia actual.
- `clock_timestamp()` es el reloj del sistema en el momento de la llamada. Cambia dentro de una misma sentencia.

**Medición (3000 conexiones nuevas, dos muestras).** Retrocesos del reloj, todos de unos 460–480 ms:

| Función | Retrocesos (muestra 1) | Retrocesos (muestra 2) |
|---|---|---|
| `now()` | 5 | 8 |
| `statement_timestamp()` | 5 | 8 |
| `clock_timestamp()` | 5 | 8 |

Los tres relojes retroceden exactamente a la vez. La causa es el reloj del sistema del entorno (la VM de Docker en la que
corre PostgreSQL), no la forma en que PostgreSQL fija el inicio de cada transacción. Cambiar a `clock_timestamp()` no
resolvería nada.

**Corrección.** Toda marca de finalización se calcula como `GREATEST(now(), created_at)`
(`analysis_ledger._completion_time`). Con eso `completed_at >= created_at` se cumple sin depender de que el reloj sea
monótono. Es segura porque:

- Solo ajusta la marca cuando el reloj iría hacia atrás; en el caso normal da exactamente `now()`.
- Las marcas siguen siendo las del servidor, nunca las del cliente.
- Los leases siguen usando `now()`. Un retroceso del reloj hace que un lease venza más tarde, lo que es el lado seguro.
  Un salto hacia delante puede hacer que venza antes; ese riesgo ya existía y se documenta aparte.

**Pruebas de regresión deterministas** (`backend/tests/test_clock_regression.py`). Escriben filas con `created_at` adelantado
al reloj y comprueban que la completación toma esa marca. Sin la guarda, dos de ellas fallan; con la guarda, pasan.

**Ordenación del historial** (verificada en `app/db/repositories.py`):

- Campañas, entradas de métricas y análisis: `created_at DESC, id DESC`.
- Intentos de un análisis: `attempt_number ASC`.
- Con marcas iguales, el desempate por `id` es determinista pero no cronológico.
- Las pruebas de paginación y de entradas de métricas comprueban el orden según las marcas almacenadas, no según el
  orden de inserción. Una prueba determinista (`test_history_is_ordered_by_recorded_time_then_id_whatever_the_insertion_order`)
  fija el contrato con marcas controladas.

**Efecto en el historial.** Si el reloj retrocede entre dos análisis, el más reciente puede aparecer detrás de uno anterior
por unos cientos de milisegundos. El orden es fiel a las marcas guardadas. Un orden estrictamente monótono requeriría una
secuencia en la base de datos; no se ha añadido.

## 9. Riesgos pendientes antes del control monetario

1. Confirmar la unidad de `cost` de OpenRouter (créditos o USD) con una respuesta real de un entorno controlado. Solo entonces
   se aplica `usd_to_micros` a `cost_micros`.
2. Saber si `cost` aparece siempre para modelos gratuitos y BYOK.
3. Peor coste por llamada (tokens máximos × precio) antes de cualquier límite monetario estricto.
4. Llamadas sin respuesta: el consumo de un timeout sigue siendo desconocido; un límite monetario debe reservar el peor caso.
5. Reloj del sistema: el historial ordena por `created_at`, que puede no ser estrictamente monótono (§8).

## 10. Visibilidad del consumo por rol

El consumo (tokens, coste y si fue reportado) es **solo del rol `owner`**, según `docs/PHASE2_DESIGN.md`, sección 4
(«Ver uso, costos y auditoría de su organización»: owner ✅, analyst ❌, client_viewer ❌).

| Endpoint | owner | analyst | client_viewer |
|---|---|---|---|
| `POST /organizations/{id}/analyses` (crea o repite) | con consumo | sin consumo | 403 |
| `GET /organizations/{id}/analyses/{analysis_id}` | con consumo | sin consumo | sin consumo |
| `GET /organizations/{id}/analyses` (historial) | sin consumo | sin consumo | sin consumo |

- Para los roles que no son owner, las claves de consumo **no aparecen** en la respuesta. No son `null` ni `0`: la respuesta
  no dice nada sobre el consumo.
- Para el owner, un consumo no reportado aparece como `usage_reported: false` con los valores en `null`. Nunca como cero.
- El rol se obtiene de la membresía que ya comprueba `require_role`. No hay consultas adicionales.
- Los esquemas son `AnalysisDetailRead` y `AnalysisAttemptRead` (operacionales) y `AnalysisDetailUsageRead` y
  `AnalysisAttemptUsageRead` (owner). Los endpoints de detalle y de creación devuelven el dict del esquema elegido; por eso
  su `response_model` es `None` y el OpenAPI de esas dos rutas no lista los campos.
- La interfaz no lee `attempts` ni ningún campo de consumo; el cambio no afecta a la pantalla.
- Pruebas: `backend/tests/test_analysis_visibility.py` (8 pruebas HTTP). Forzar `with_usage=True` para todos los roles
  hace fallar 5 de ellas.
