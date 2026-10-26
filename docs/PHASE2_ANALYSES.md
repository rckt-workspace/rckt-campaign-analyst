# Fase 2.5A: persistencia de análisis y registro de uso (diseño)

Estado: **diseño y migración para revisión**. No hay endpoints nuevos. Ejecutar la migración en Lovable Cloud
requiere aprobación. Base: rama `feat/phase-2-4-campaign-persistence` (PR #2, Draft).

Migraciones:
- SQL portable: `supabase/migrations/20261025000300_analyses.sql`
- Alembic equivalente: `backend/migrations/versions/20261025_0004_analyses.py` (revisión `0004_analyses`)

## 1. Tablas

| Tabla | Propósito | Filas |
|---|---|---|
| `analyses` | Una solicitud de informe. También es la reclamación de idempotencia. | Se crean en `running`; se completan una vez |
| `analysis_usage` | Un intento al proveedor (`primary` o `fallback`). | Una por tipo y análisis; solo se completan |

Cambio incremental adicional: restricción `UNIQUE (organization_id, campaign_id, id)` en
`campaign_metric_entries`, necesaria como destino de la clave compuesta. No modifica datos ni migraciones anteriores.

### `analyses`
- Identidad y aislamiento: `organization_id`, `requested_by`, `campaign_id` opcional, `metric_entry_id` opcional.
- Idempotencia: `idempotency_key` (1–128 caracteres), `UNIQUE (organization_id, idempotency_key)`.
- Entrada: `input_hash` (SHA-256 en hexadecimal, 64 caracteres), `prompt_version`, `model_requested`.
- Estado: `status` (`running`, `succeeded`, `failed`), `lease_expires_at`, `completed_at`, `error_code`.
- Resultado: `analysis_markdown`, `deterministic_report` (objeto JSON), `model_used`, `used_fallback`, `latency_ms`.

### `analysis_usage`
- `attempt_kind` (`primary`, `fallback`), `UNIQUE (analysis_id, attempt_kind)`.
- `status`: `reserved` (existe antes de llamar al proveedor), `succeeded`, `failed`.
- `usage_reported`: indica si el proveedor devolvió el uso. `prompt_tokens`, `completion_tokens` y `cost_micros`
  solo pueden tener valor si `usage_reported` es verdadero (restricción). **Nunca se estiman.**

## 2. Aislamiento

- Cada tabla tiene `organization_id` no nulo.
- `analyses` referencia `campaigns (organization_id, id)` y `campaign_metric_entries (organization_id, campaign_id, id)`.
  Una entrada solo puede asociarse a su propia campaña, y ambas a la misma organización.
- `analysis_usage` referencia `analyses (organization_id, id)`: un intento no puede apuntar a un análisis ajeno.
- Una entrada de métricas sin campaña se rechaza (`ck_analyses_metric_entry_needs_campaign`).

## 3. Estados

Análisis: `running` → `succeeded` | `failed`. No hay otras transiciones. Un análisis completado y sus entradas
(`organization_id`, `campaign_id`, `metric_entry_id`, `requested_by`, `idempotency_key`, `input_hash`,
`prompt_version`, `model_requested`, `deterministic_report`, `created_at`) no cambia nunca.

Intento: `reserved` → `succeeded` | `failed`. Un intento registrado no cambia ni se borra.

Códigos de error permitidos (fijos, sin texto del proveedor): `AI_TIMEOUT`, `AI_UNAVAILABLE`,
`AI_INVALID_RESPONSE`, `AI_NOT_CONFIGURED`, `ANALYSIS_ABANDONED`, `INTERNAL_ERROR`. Cualquier otro valor se rechaza.

Fallback: se registran dos intentos como máximo. Si el primario falla y el fallback responde, `used_fallback` es
verdadero y `model_used` es el modelo del fallback. Si ambos fallan, el análisis queda `failed` con el código del último intento.

## 4. Idempotencia y concurrencia

La reclamación es una sola sentencia:

```sql
INSERT INTO analyses (...) VALUES (...)
ON CONFLICT (organization_id, idempotency_key) DO NOTHING
RETURNING id;
```

Solo una de las inserciones concurrentes con la misma clave devuelve fila. El resto no llama al proveedor.
Probado con 8 reclamaciones simultáneas en conexiones distintas: exactamente una gana.

Decisiones para cada caso (a implementar en la fase 2.5B):

| Situación al reintentar con la misma clave | Respuesta | Llamada al proveedor |
|---|---|---|
| Reclamación ganada, la petición original sigue en curso | 409 `ANALYSIS_IN_PROGRESS` | No |
| `succeeded`, mismo `input_hash` | 200 con el informe guardado | No |
| `failed`, mismo `input_hash` | Fallo guardado (mismo código seguro) | No |
| `running` con lease vencido | Pasa a `failed` con `ANALYSIS_ABANDONED` (una sola transición condicionada) | No |
| Misma clave, `input_hash` distinto | 422 `IDEMPOTENCY_KEY_REUSED` | No |

Reglas de seguridad:
- El `lease` debe superar el presupuesto de tiempo del proveedor (hoy 110 s) con margen. Si el lease vence mientras
  la llamada sigue en curso, el propietario ya no podría escribir el resultado; por eso no se toma un análisis
  `running` vigente.
- Un análisis `running` con lease vencido nunca se reintenta con la misma clave. Si el proveedor llegó a cobrar,
  el intento queda `reserved` como evidencia y no se registran tokens.
- Si el propietario sigue vivo tras vencer el lease y termina, su `UPDATE` a `succeeded` falla por el trigger
  (el análisis ya está `failed`). El resultado se descarta. Es la única ventana de cobro sin registro; se mitiga
  con el margen del lease y con el registro `reserved` previo a la llamada.
- Un fallo "failed" no se reintenta automáticamente con la misma clave: quien quiera reintentar envía una clave nueva.

## 5. Inmutabilidad (triggers)

- `analyses`: no se borra; una fila completada no se modifica; en `running` solo cambian estado, lease y resultado
  (no los campos de entrada).
- `analysis_usage`: no se borra; un intento `reserved` puede completarse una vez; un intento completado no cambia.
- `TRUNCATE` no se bloquea, igual que en `campaign_metric_entries` (solo lo usan pruebas y administración).

## 6. Seguridad y acceso

- RLS habilitado en ambas tablas, **sin políticas**. Los roles expuestos por las APIs gestionadas (`anon`,
  `authenticated`) no reciben filas. El backend accede como propietario (`ENABLE`, no `FORCE`).
- No se añadieron `GRANT` a `anon` ni `authenticated`, igual que en las migraciones 0002 y 0003.
- **Pendiente de decisión:** `CLAUDE.md` indica que las tablas nuevas necesitan `GRANT` además de RLS. Mantengo el
  patrón ya aplicado y probado porque el objetivo declarado es acceso cerrado hasta validar la identidad real.
  Revisar con Lovable antes de abrir cualquier acceso desde la API de datos.
- Ningún secreto, cuerpo de proveedor ni URL se guarda en ninguna columna.

## 7. Uso y costos

- Hoy `openrouter.py` devuelve solo el texto de la respuesta (`ChatResult`). No lee el campo `usage`.
  Por tanto, `usage_reported` será falso y tokens y costo quedarán nulos hasta que se implemente la lectura de `usage`.
- Antes de registrar costos: verificar el formato real de `usage` con una llamada autorizada (requisito de
  `PHASE2_DESIGN.md`, sección 3.3). No se hacen llamadas en esta fase.

## 8. Verificación

- `backend/tests/test_analysis_schema.py` (21 pruebas): RLS sin políticas, ida y vuelta de la migración, aislamiento
  entre organizaciones en campañas, entradas y intentos, unicidad de la clave, 8 reclamaciones concurrentes
  (una ganadora), estados y formatos inválidos (uno por restricción), códigos seguros, inmutabilidad y registro de intentos.
- `backend/tests/test_persistence.py`: lista de restricciones CHECK actualizada con las 22 nuevas.
- Equivalencia SQL/Alembic: la migración SQL se aplicó a una base de comprobación aislada
  (`rckt_sqlcheck_test`, creada y eliminada en el contenedor local). Tablas, índices, restricciones y triggers
  coinciden con `rckt_test`; las dos funciones de trigger coinciden salvo sangría.

## 9. Riesgos abiertos

1. Ventana de cobro sin registro: el propietario puede llamar al proveedor, perder el lease y no poder escribir.
   Mitigación: margen del lease, `reserved` previo, no reintento con la misma clave. Riesgo residual documentado.
2. Ausencia de tokens y costos reales hasta leer `usage`. No hay cuota fiable hasta la fase 2.6.
3. Políticas RLS y GRANT pendientes de decisión, ver sección 6.
4. El límite de 100 000 caracteres del informe debe confirmarse frente al tamaño real de los informes.
5. Antes de la fase 2.5B: definir el encabezado `Idempotency-Key` frente a un campo del cuerpo.

## 10. Lo que no se implementa todavía

Endpoints de análisis, lectura del historial, lectura de `usage` en el proveedor, cuotas, facturación y pantallas.
Se detiene aquí para revisar la estructura de datos.
