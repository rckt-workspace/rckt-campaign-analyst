# Fase 2.5A: persistencia de análisis y registro de llamadas (diseño revisado)

Estado: **esquema, protocolo de escritura y pruebas listos para revisión**. No hay endpoints de análisis. La migración
no se ha ejecutado en Lovable Cloud ni en ningún entorno remoto. Base: `feat/phase-2-5-analysis-history`, sobre
`feat/phase-2-4-campaign-persistence` (PR #2, Draft).

Migraciones (la 0004 se puede corregir porque no se ha aplicado; 0001, 0002 y 0003 no se tocan):
- SQL portable: `supabase/migrations/20261025000300_analyses.sql` (331 líneas, revisado completo)
- Alembic: `backend/migrations/versions/20261025_0004_analyses.py`. Sus sentencias se **generan** desde el SQL con
  `gen_alembic_0004.py`; no se editan a mano.

## 1. Decisiones aprobadas

1. **Acceso cerrado.** RLS habilitado en ambas tablas, sin políticas, sin GRANT a `anon` ni `authenticated`. `CLAUDE.md`
   lo recoge. `AGENTS.md` todavía dice que las tablas nuevas necesitan GRANT: conflicto pendiente de resolver.
2. **Límite de 100 000 caracteres** para `analysis_markdown`.
3. **Cabecera `Idempotency-Key`** en la solicitud de análisis (se implementará en la fase 2.5B). El valor se guarda
   como `idempotency_key`; el hash de entrada se guarda como `input_hash`.
4. **Solo tokens y costos que el proveedor reporte.** Si no los reporta, las columnas quedan nulas. Nunca se estiman.

## 2. Modelo de llamadas

Una solicitud puede hacer hasta **cuatro llamadas HTTP**: `initial` (primario y, si falla, fallback) y, si la
respuesta no cumple el formato, `format_retry` (primario y, si falla, fallback). `analysis.py` y `openrouter.py`
muestran este flujo, y el modelo anterior, con un único intento por tipo, no lo representaba.

`analysis_usage` tiene **una fila por cada llamada HTTP efectivamente intentada**:

| Columna | Valores | Función |
|---|---|---|
| `attempt_number` | 1–4, consecutivo | Orden de las llamadas. `UNIQUE (analysis_id, attempt_number)`. |
| `step` | `initial`, `format_retry` | Por qué se llama. |
| `model_role` | `primary`, `fallback` | Qué modelo configurado se usa. |
| `UNIQUE (analysis_id, step, model_role)` | | Impide duplicados accidentales dentro de un paso. |
| `status` | `reserved`, `succeeded`, `failed`, `unknown` | `unknown`: no se puede confirmar el resultado ni el cobro. |
| `error_code` | Lista cerrada (ver §5) | Nunca texto del proveedor. |
| `http_status` | 100–599 o nulo | Número de estado HTTP; no el cuerpo. |
| `usage_reported`, `prompt_tokens`, `completion_tokens`, `cost_micros` | | Solo con `usage_reported = true`. |

Reglas de inserción (triggers, ver §4). Un intento solo se reserva si el análisis está `running` con lease vigente,
ningún otro intento está `reserved`, el número es el siguiente, el primer intento es `initial/primary`, un fallback
sigue a un primario fallido del mismo paso y un reintento de formato sigue a una respuesta `initial` correcta.

## 3. Análisis: estados y datos

`analyses` se crea en `running` con su **informe determinístico** (`deterministic_report`, obligatorio), su hash de
entrada, versión de prompt y modelo solicitado. Esos campos son inmutables desde ese momento.

- `running` → `succeeded` | `failed`. Sin otras transiciones.
- `succeeded` exige informe no vacío, `model_used`, `format_valid` y un intento `succeeded` con ese modelo y el rol
  indicado por `used_fallback`.
- `failed` exige `error_code` y no tiene informe.
- `format_valid` registra si el texto guardado cumple las secciones obligatorias. Un informe que no las cumple se guarda
  como `succeeded` con `format_valid = false`, igual que hace hoy el servicio. Es una decisión de la fase 2.5B si
  debe cambiar.

## 4. Protocolo de escritura y concurrencia

Implementado en `backend/app/db/analysis_ledger.py`. Cada función es **una transacción corta**; ninguna queda abierta
durante una llamada HTTP:

1. `claim_analysis`: `INSERT … ON CONFLICT DO NOTHING` y commit **antes** de cualquier llamada. Resultados posibles:
   `claimed` (única vía que permite llamar al proveedor), `existing`, `expired`, `reused`.
2. Por cada llamada: `reserve_attempt` (commit) → llamada al proveedor sin transacción abierta → `complete_attempt`.
3. `finalize_analysis` (commit).
4. `abandon_expired`: solo con lease vencido. Marca los intentos `reserved` como `unknown` y el análisis como `failed`
   con `ANALYSIS_ABANDONED`, en una transacción. **No hace llamadas.**

Garantías, comprobadas contra PostgreSQL con proveedor simulado (`test_analysis_ledger.py`, 23 pruebas):
- Dos solicitudes simultáneas con la misma clave: una sola reclamación gana; solo esa llama al proveedor.
- La misma clave con datos distintos: `reused`, sin llamada.
- Clave de análisis completado: devuelve el resultado guardado, sin llamada.
- Lease vencido: `expired`; se abandona y la misma clave nunca vuelve a llamar al proveedor.
- Un propietario con lease vencido no puede reservar, completar ni finalizar. La base lo rechaza.
- Un propietario "zombi" no puede sobrescribir un análisis abandonado.
- La reserva y la reclamación son visibles en otra conexión **antes** de que el proveedor responda.

### Límites: lo que no se garantiza

- **No hay exactamente-una-ejecución frente a fallos de red.** Si el proveedor cobra y la respuesta se pierde, la
  base solo sabe que el intento está `reserved` y después `unknown`. El cobro puede existir sin registro de tokens.
- **Lease.** Debe superar el presupuesto total de la solicitud: hasta cuatro llamadas de `CHAT_TIMEOUT_MS` (45 s por
  defecto, 120 s como máximo). La fase 2.5B fijará el valor; el margen evita que un propietario vivo pierda el lease.
- **Lease vencido con llamada en curso.** El propietario ya no puede escribir: su resultado se descarta. El cobro, si
  llegó a producirse, queda como `unknown`. Es la ventana de riesgo residual más relevante.
- **Reintento con la misma clave tras un fallo.** No se reintenta automáticamente. Quien quiera reintentar envía una
  clave nueva.

## 5. Códigos de error

Lista cerrada en `app/domain/analysis.py` y en las restricciones SQL. Cada código proviene de `openrouter.py`:

| Código | Origen |
|---|---|
| `AI_TIMEOUT` | Tiempo agotado (`CHAT_TIMEOUT_MS`). |
| `AI_NETWORK` | Fallo de transporte antes de respuesta. |
| `AI_EMPTY` | Respuesta sin texto utilizable. |
| `AI_HTTP_ERROR` | Estado HTTP no exitoso. El número va en `http_status`. |
| `AI_NOT_CONFIGURED` | Configuración incompleta; no se llama al proveedor. |
| `ANALYSIS_ABANDONED` | Lease vencido mientras el análisis estaba en curso. |
| `INTERNAL_ERROR` | Fallo interno no clasificado. |

El código original `AI_<status>` (p. ej. `AI_502`) no se guarda: se normaliza a `AI_HTTP_ERROR` + `http_status`.

## 6. Uso y costos

- Hoy `openrouter.py` devuelve solo el texto. **No lee `usage`**, así que ninguna llamada real tiene tokens ni costo.
- Antes de registrar costos: verificar el formato real de `usage` con una llamada autorizada, según
  `PHASE2_DESIGN.md` §3.3. En esta fase no se hacen llamadas.

## 7. Seguridad

- Ningún secreto, cuerpo de proveedor ni URL se guarda en ninguna columna.
- Aislamiento: cada clave foránea incluye `organization_id`. Una entrada de métricas se referencia con su campaña.
  Un intento no puede escribirse en un análisis de otra organización.

## 8. Verificación

| Comprobación | Resultado |
|---|---|
| `test_analysis_schema.py` (constraints, triggers, aislamiento, lease, inmutabilidad) | 35 passed |
| `test_analysis_ledger.py` (protocolo con proveedor simulado) | 23 passed |
| `test_persistence.py` (lista de CHECK) | 25 nombres de análisis actualizados |
| Suite backend completa | 222 passed |
| SQL portable aplicado a base aislada `rckt_sqlcheck_test` vs. Alembic en `rckt_test` | Tablas, índices, restricciones y triggers sin diferencias; 45 nombres de restricción idénticos; 3 funciones idénticas (ignorando espacios) |

La base `rckt_test` se recreó para construir el esquema desde cero. Todas las pruebas usan solo bases `*_test` locales.

## 9. Riesgos abiertos

1. Cobro sin registro de tokens en la ventana de lease vencido (ver §4).
2. Sin tokens ni costos reales hasta leer `usage`. No hay cuota fiable hasta la fase 2.6.
3. `AGENTS.md` contradice `CLAUDE.md` sobre GRANT. Sin resolver, la migración sigue el criterio de acceso cerrado.
4. `format_valid = false` se guarda como éxito. Decidir en 2.5B.
5. El lease debe fijarse con el presupuesto real de cuatro llamadas.

## 10. Fuera de alcance

Endpoints de análisis, cabecera `Idempotency-Key` en la API, lectura del historial, lectura de `usage`, cuotas,
facturación y pantallas. Se detiene aquí para revisar la estructura de datos.
