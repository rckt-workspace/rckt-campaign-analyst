# Fase 2.6B2B: auditoría mínima de operaciones

Estado: implementada en `feat/phase-2-6b2-audit-events`, apilada sobre `feat/phase-2-6b2-usage-reports`. Registra las
operaciones que existen hoy. No incluye `platform_admin`, dashboards ni endurecimiento de producción.

## 1. Eventos implementados

| Evento | Cuándo | Origen | Actor | Recurso | Metadata permitida |
|---|---|---|---|---|---|
| `analysis.created` | Solicitud nueva aceptada (reclamación) | `user` | usuario que solicita | `analysis` | `quota_period` (`YYYY-MM` o null) |
| `analysis.succeeded` | Análisis finalizado correctamente | `internal` | ninguno | `analysis` | `model_used`, `used_fallback`, `format_valid` |
| `analysis.failed` | Finalizado sin éxito | `internal` | ninguno | `analysis` | `error_code` (código seguro), `used_fallback` |
| `analysis.failed` | Abandono por lease vencido | `internal` | ninguno | `analysis` | `error_code` = `ANALYSIS_ABANDONED`, `used_fallback` = false |
| `quota.updated` | Cambio real de cuota por CLI | `operator_cli` | ninguno | `analysis_quota` (id de la organización) | `plan`, `monthly_analysis_limit`, `previous_plan`, `previous_monthly_analysis_limit` |
| `membership.granted` | Membresía concedida por CLI | `operator_cli` | ninguno | `organization_membership` | `role` |
| `usage.report_accessed` | Consulta autorizada del informe de consumo | `user` | owner que consulta | `usage_report` (`YYYY-MM`) | `period` |

No hay eventos para campañas, creación de organizaciones ni provisión de usuarios. No están en la lista de operaciones
auditadas de esta fase; se anotan como hueco.

## 2. Modelo de datos (migración 0008)

`audit_events` (solo inserción):

- `id` `bigint` de identidad: ordenable y estable como cursor.
- `organization_id` obligatorio, clave foránea con `RESTRICT`.
- `occurred_at` `timestamptz` generado por PostgreSQL (`now()` por defecto).
- `source`: `user`, `internal` o `operator_cli`. Una CHECK exige que `actor_user_id` exista si y solo si la fuente es `user`.
- `action` y `resource_type`: códigos fijos, con CHECK.
- `resource_id` texto no vacío, de hasta 128 caracteres.
- `metadata` `jsonb`: objeto de hasta 2048 bytes. Las claves permitidas se validan en la aplicación, antes de llegar a la base.

Garantías en la base:

- Un trigger `BEFORE UPDATE OR DELETE` rechaza ambas operaciones: `audit_events is append-only`.
- RLS activo sin políticas y sin `GRANT` para `anon`, `authenticated` ni `PUBLIC`, igual que el resto del esquema.
- Índice `(organization_id, id DESC)` para la lectura paginada.

La migración solo crea estructura: no inserta ni reconstruye eventos. Los análisis anteriores a esta fase no tienen evento.

## 3. Origen y atribución

- **`user`**: una persona autenticada. `actor_user_id` es esa persona.
- **`internal`**: un proceso de la aplicación (finalizar o abandonar un análisis). No tiene actor.
- **`operator_cli`**: una operación de operador ejecutada en el servidor. **No tiene actor**: ningún usuario verificado está
  detrás de la acción, así que no se inventa uno. Esto no equivale a una atribución personal verificada: el evento dice
  que un operador lo hizo desde el servidor, no quién.

## 4. Garantías transaccionales

Las escrituras de negocio y su evento se confirman o se revierten **en la misma transacción**:

- **Reclamación de análisis** (`claim_analysis`): el análisis, la unidad de cuota y `analysis.created` van juntos. Una cuota
  agotada revierte todo: sin análisis, sin unidad y sin evento.
- **Finalización** (`finalize_analysis`): el cambio de estado, la liberación de cuota si aplica y el evento van juntos.
  Si el evento no se puede escribir, el análisis no cambia de estado.
- **Abandono** (`abandon_expired`): igual. Un abandono que no cierra nada no escribe evento.
- **Cuota por CLI** (`set-analysis-quota`): el cambio y su evento van juntos. Repetir la misma configuración no escribe nada.
- **Membresía por CLI** (`grant-membership`): la membresía y su evento van juntos.

**Lectura del reporte de consumo.** Las consultas agregadas se hacen en modo solo lectura. Después de la autorización y
antes de entregar la respuesta, se escribe `usage.report_accessed` en una transacción propia. Si esa escritura falla, la
respuesta es 503 `AUDIT_UNAVAILABLE` y el informe no se entrega.

**Accesos rechazados.** Un 401, 403 o 404 no escribe ningún evento de acceso.

**Replays.** Un replay idempotente no escribe `analysis.created` ni ningún otro evento nuevo.

Dos límites reconocidos:

- Si el proceso cae después de confirmar `usage.report_accessed` y antes de responder, el evento existe sin entrega. Es un
  acceso autorizado que ocurrió; no contradice la garantía.
- Un error inesperado de la aplicación revierte la operación y su evento juntos, pero un error de auditoría no se disfraza:
  se propaga.

## 5. Consulta

```
GET /api/v1/organizations/{organization_id}/audit-events?cursor=&limit=
```

| Caso | Respuesta |
|---|---|
| Sin autenticación | 401 |
| Sin membresía | 404 `NOT_FOUND` |
| Miembro que no es owner | 403 `FORBIDDEN` |
| `limit` fuera de 1–100, `cursor` no positivo | 422 |
| Owner | 200 `AuditEventPageRead` |

- Orden: `id` descendente, el más reciente primero.
- Cursor: `next_cursor` es el `id` del último evento de la página. La página siguiente empieza por debajo. Es `null` si no hay más.
- `limit` por defecto 50, máximo 100.
- No existe ningún endpoint para modificar ni eliminar eventos.

Ejemplo de respuesta (simulada, con el análisis del escenario de pruebas):

```json
{
  "items": [
    {
      "id": 6,
      "occurred_at": "2026-10-27T16:20:01Z",
      "source": "internal",
      "actor_user_id": null,
      "action": "analysis.succeeded",
      "resource_type": "analysis",
      "resource_id": "b7c1-example",
      "metadata": {"model_used": "google/gemma-3-27b-it", "used_fallback": false, "format_valid": true}
    },
    {
      "id": 5,
      "occurred_at": "2026-10-27T16:20:00Z",
      "source": "user",
      "actor_user_id": "0f3a-example",
      "action": "analysis.created",
      "resource_type": "analysis",
      "resource_id": "b7c1-example",
      "metadata": {"quota_period": "2026-10"}
    }
  ],
  "next_cursor": null
}
```

## 6. Seguridad de los registros

- **Metadata por lista blanca explícita.** Cada acción declara sus claves y sus tipos en `app/domain/audit.py`. Cualquier
  otra clave se rechaza antes de escribir.
- **Nunca se copian objetos arbitrarios.** Cada evento construye su metadata campo a campo.
- **Lo que no se guarda:** contraseñas, tokens, claves de API, prompts, respuestas completas del proveedor, textos de
  campañas, cuerpos de solicitudes, credenciales de PostgreSQL e información personal innecesaria.
- Los códigos de error son solo los de `SAFE_ERROR_CODES`.
- Las pruebas buscan, en todos los eventos, marcadores de texto del usuario, de la clave de prueba y de tokens.

## 7. Límites y riesgos

1. **Orden del cursor con escrituras concurrentes.** La identidad de PostgreSQL asigna el `id` al insertar, no al
   confirmar. Un evento confirmado después con un `id` menor puede no aparecer en una página ya leída. Para un historial
   de consulta es aceptable; para un registro de cumplimiento estricto habría que retrasar la lectura hasta un `id`
   estable.
2. **TRUNCATE no está bloqueado por trigger.** Igual que las tablas de análisis. El rol de aplicación en producción no
   debe tener `TRUNCATE`; es una decisión de despliegue pendiente.
3. **El rol propietario puede modificar el esquema.** Los triggers protegen frente a la aplicación, no frente a quien puede
   quitar el trigger. La separación de roles está en el plan de producción, no aquí.
4. **Atribución de la CLI.** `operator_cli` no identifica a una persona. Antes de un piloto, el procedimiento debería
   registrar también al operador, con su propia verificación.
5. **Mes de la cuota y reloj.** Heredado de 2.6B2A: el mes de cuota lo fija el reloj de Python y `created_at` el de
   PostgreSQL. Es un pendiente obligatorio antes del piloto. No se cambia en esta fase.
6. **Cobertura parcial.** Campañas, `create-organization` y `provision-user` no se auditan.
7. **Sin reconstrucción.** Los eventos de operaciones anteriores a la migración no existen y no se inventan.

## 8. Pruebas

`backend/tests/test_audit_events.py` (40 pruebas): lista blanca y reglas de origen, tipos de valor, eventos reales
(creación, réplica, éxito, fallo, abandono, cuota agotada, cambio de cuota por CLI, membresía por CLI), rollback cuando
falla el registro obligatorio, lectura del informe con y sin evento, accesos rechazados, aislamiento entre
organizaciones, paginación y orden, validación de parámetros, inmutabilidad en la base, RLS y ausencia de grants,
ausencia de secretos y textos sensibles, migración sin inserciones, contrato OpenAPI y forma exacta de la respuesta.

## 9. Dependencias

- Requiere las migraciones 0001–0008. La 0008 no modifica ninguna anterior.
- Pendiente en `rckt` (sigue en 0005), en Lovable Cloud y en Supabase licenciado. No aplicada en ningún entorno real.
