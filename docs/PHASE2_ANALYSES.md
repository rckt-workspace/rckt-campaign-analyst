# Fase 2.5A: persistencia de análisis y registro de llamadas (diseño revisado)

Estado: **fase 2.5B implementada** (endpoints, historial e interfaz, §11). La migración 0004 está aplicada en la base
local de desarrollo y en las bases de prueba; **no** se ha ejecutado en Lovable Cloud ni en ningún entorno remoto. Base: `feat/phase-2-5-analysis-history`, sobre
`feat/phase-2-4-campaign-persistence` (PR #2, Draft).

Migraciones (0001 a 0003 no se tocan. La 0004 está aplicada en la base local y es inmutable: no se edita. Las correcciones van en migraciones nuevas: la 0005 es la compuerta de formato):
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

## 10. Fuera de alcance de la fase 2.5A

Endpoints de análisis, cabecera `Idempotency-Key` en la API, lectura del historial, lectura de `usage`, cuotas,
facturación y pantallas. Se detiene aquí para revisar la estructura de datos.

## 11. Implementación 2.5B

### Endpoints (`backend/app/api/analysis_routes.py`)

| Método y ruta | Roles | Respuesta |
|---|---|---|
| `POST /api/v1/organizations/{org}/analyses` (cabecera `Idempotency-Key`) | owner, analyst | 201 nuevo; 200 + `Idempotent-Replayed: true` si se repite |
| `GET /api/v1/organizations/{org}/analyses?limit&offset` | todos | Página, más reciente primero, sin texto del informe |
| `GET /api/v1/organizations/{org}/analyses/{id}` | todos | Informe, reporte determinista e intentos |

Códigos: `IDEMPOTENCY_KEY_REQUIRED` y `IDEMPOTENCY_KEY_INVALID` (400), `FORBIDDEN` (403), `NOT_FOUND` (404),
`ANALYSIS_IN_PROGRESS` (409), `IDEMPOTENCY_KEY_REUSED` (422). Un análisis fallido responde 201 o 200 con
`status: failed` y su código seguro: el fallo es un resultado guardado, no un error HTTP.

La clave admite de 8 a 128 caracteres: letras, cifras, `.`, `_`, `:` y `-`.

### Flujo de una creación

1. Autenticación y membresía en transacciones cortas (`_resolve_user`, `_authorize`). La ruta no depende de
   `get_db_session`, porque esa dependencia mantendría una transacción abierta durante toda la petición.
2. `analysis_history.create_analysis`: calcula el reporte determinista y la huella de entrada, y reclama la clave en
   PostgreSQL (`analysis_ledger.claim_analysis`). Resultados: `claimed` (única vía que llama al proveedor), `existing`,
   `expired`, `reused`.
3. `claimed`: `_run_provider_flow` llama a `request_campaign_analysis` con un `AttemptRecorder`. Cada llamada HTTP se
   reserva antes (`reserve_attempt`, commit) y se completa después (`complete_attempt`, commit). Ninguna transacción
   queda abierta durante la espera al proveedor.
4. Cierre: `succeeded` si el texto cumple el formato; si no, `failed` con `AI_INVALID_RESPONSE`. Cualquier fallo del
   proveedor queda como `failed` con el último código seguro. Si la base rechaza la escritura porque el lease venció,
   el análisis se cierra como `ANALYSIS_ABANDONED`.

### Cambios en código existente (compatibles)

- `run_chat_completion` acepta un observador opcional. Sin él, la llamada es idéntica a la anterior.
- `request_campaign_analysis` acepta un registrador opcional por paso (`initial`, `format_retry`).
- El endpoint anterior `POST /campaigns/analyze` se mantiene sin cambios de contrato y sin persistencia.
- El frontend usa ahora el endpoint persistente. Su fallback de un intento sin formato sigue siendo el mismo.

### Interfaz

- Panel «Historial de informes» junto al de campañas guardadas, con el mismo diseño. Muestra campaña, fecha y estado
  con texto seguro. Carga de más resultados (`offset`).
- Abrir un informe anterior reutiliza el panel de informe ejecutivo. Un análisis sin informe muestra su estado.
- Clave de idempotencia: una por análisis, generada en el navegador. El reintento de un análisis que terminó en error
  del navegador o de red **reutiliza la misma clave** (no hay segundo cobro). Un fallo guardado por el servidor exige
  una clave nueva, porque repetir la clave devolvería el mismo fallo.
- El tiempo de espera del navegador es de 200 s: cubre cuatro llamadas con el timeout por defecto (45 s).

### Decisiones

- **Formato inválido tras el reintento:** `failed` con `AI_INVALID_RESPONSE`. Antes, el servicio devolvía el primer
  texto aunque no cumpliera el formato. Solo cambia el flujo nuevo; el endpoint anterior sigue igual.
- **`AI_INVALID_RESPONSE`** se añadió a la lista segura de la 0004 **antes de la primera aplicación local**. Ninguna
  base la ha recibido sin ese código. A partir de ahí, la 0004 es inmutable; cualquier cambio posterior va en una migración nueva.
- **Lease = 510 s** (cuatro llamadas a 120 s, el máximo permitido, más margen). Un lease más largo solo retrasa el
  abandono.
- **Tokens y costo:** el proveedor no devuelve `usage` en el transporte actual, así que `usage_reported` es siempre
  falso y las columnas quedan nulas. No se estiman.

### Limitaciones frente a fallos

- **No hay exactamente-una-ejecución.** Si el proveedor cobra y la respuesta se pierde, el intento queda `reserved`
  y después `unknown` (con `ANALYSIS_ABANDONED` si el lease venció). El cobro puede existir sin tokens registrados.
- **Lease vencido con llamada en curso:** el propietario ya no puede escribir. Su resultado se descarta y el análisis se
  abandona. Es la ventana de riesgo residual más importante.
- **Cierre fallido tras un error de base:** si el análisis no puede cerrarse (la base rechaza la escritura y el lease
  sigue vigente), queda en `running` y se cierra en la siguiente petición con la misma clave, cuando el lease venza.
- **Carrera entre páginas del historial:** si llegan análisis nuevos entre dos cargas de «Cargar más», el `offset`
  puede repetir un elemento. Es aceptable para esta fase; el cursor por fecha queda para más adelante.

### Verificación

| Comprobación | Resultado |
|---|---|
| Backend (pytest), suite completa | 257 passed (incluye 21 de API, 35 de esquema y 23 de protocolo) |
| Pruebas unitarias (`npm run test:unit`) | 25 passed |
| TypeScript | sin errores |
| Build Node/Nitro | correcto |
| Playwright (servicios simulados) | 32 passed (4 nuevas de historial) |
| Docker Compose | config válida; `backend`, `frontend` y `db` en healthy; migración 0004 aplicada a la base local |

Todas las pruebas usan proveedores simulados (transporte de prueba o respuestas guionadas). No hay llamadas a
OpenRouter ni cobros.

### Pendiente

- Leer `usage` de OpenRouter (requiere una llamada autorizada, `PHASE2_DESIGN.md` §3.3).
- Resolver el conflicto GRANT entre `AGENTS.md` y `CLAUDE.md` antes de abrir la API de datos.
- Cuotas y facturación (fase 2.6).
- Cursor por fecha para el historial en lugar de `offset`.

## 12. Auditoría de cierre (2026-10-26)

### Rol de FastAPI y privilegios

| Hecho comprobado (base local `rckt`) | Consecuencia |
|---|---|
| FastAPI se conecta como `rckt`, propietario de todas las tablas, **superusuario** y con `BYPASSRLS` | Como propietario, RLS no restringe al backend. Por eso la protección de RLS es frente a `anon` y `authenticated`. |
| `FORCE ROW LEVEL SECURITY` desactivado | Activarlo sin políticas para el backend dejaría al backend sin filas. No se activa. |
| Los roles `anon` y `authenticated` no existen en la base local | Su acceso no se puede probar localmente. Solo se puede comprobar en Lovable con `information_schema.role_table_grants`. |
| No hay GRANT ni políticas para esos roles en el repositorio | Las migraciones 0002 a 0005 no añaden acceso. |

Para producción, el rol del backend debe ser propietario **no superusuario**, o un rol de aplicación sin `BYPASSRLS`
con los privilegios mínimos. Esto está pendiente de comprobar en el proyecto gestionado.

Recomendación, no aplicada: una migración defensiva con `REVOKE ALL` sobre las tablas de análisis y de campañas
para `anon` y `authenticated`, dentro de un bloque que compruebe que el rol existe. Requiere aprobación.

### Hallazgos y correcciones

| Hallazgo | Estado |
|---|---|
| Un timeout de httpx se aplica por fase (conexión, escritura, pool y lectura). Una llamada podía tardar hasta cuatro veces `CHAT_TIMEOUT_MS`, y el lease de 510 s no lo cubría en el peor caso. | **Corregido.** `call_model` aplica un plazo total con `asyncio.timeout`. Cuatro llamadas a 120 s suman 480 s, dentro del lease. Prueba: `test_one_call_never_exceeds_its_total_deadline`. |
| Un error inesperado (no del proveedor) dejaba el intento `reserved` y el análisis `running` hasta que venciera el lease. | **Corregido.** El observador registra `INTERNAL_ERROR` y el servicio cierra el análisis como `failed`; después relanza el error. Prueba: `test_an_unexpected_failure_never_leaves_the_analysis_running`. |
| El esquema permitía `succeeded` con `format_valid = false`, contra la decisión aprobada. | **Corregido** con la migración nueva **0005**. La 0004 no se modifica: está aplicada en local. |
| Las pruebas del protocolo esperaban el comportamiento antiguo para formatos inválidos. | **Corregido.** Ahora esperan `failed` con `AI_INVALID_RESPONSE`. |
| `AGENTS.md` y `CLAUDE.md` se contradecían sobre GRANT. | **Corregido.** Ambos documentan el acceso cerrado. `AGENTS.md` se editó fuera del bloque sincronizado con Lovable; hay que revisarlo. |

Sin hallazgos en: concurrencia (una sola llamada con la misma clave), aislamiento por organización, transacciones
cortas, compatibilidad del endpoint anterior, duplicación de consumo y exposición de secretos en el bloque del navegador.

### Presupuesto de tiempo

- Por llamada: como máximo `CHAT_TIMEOUT_MS`, con un tope de 120 s.
- Por paso: primario y fallback, es decir, dos llamadas.
- Por análisis: dos pasos, es decir, cuatro llamadas. Peor caso con el máximo configurado: 480 s, más el coste de la base.
- Lease: 510 s. Por defecto (45 s): 180 s en el peor caso.
- Frontend: espera 200 s. Si el navegador abandona antes, la misma clave devuelve el resultado o el estado en curso al reintentar, sin segundo cobro.

### Costos y tokens desconocidos

Verificado con pruebas: cuando el proveedor no reporta `usage`, `usage_reported` es falso y `prompt_tokens`,
`completion_tokens` y `cost_micros` quedan `NULL`. El transporte actual nunca lee `usage`.

### Verificación de la auditoría

| Comprobación | Resultado |
|---|---|
| Backend, suite completa | 260 passed (incluye la compuerta de formato de la 0005) |
| Pruebas unitarias | 25 passed |
| TypeScript y build Node/Nitro | correctos |
| Playwright (servicios simulados) | 32 passed |
| Esquema SQL portable 0001–0005 frente a Alembic (base aislada) | sin diferencias |
| Esquema de la base local `rckt` frente a la de prueba | sin diferencias; cabeza `0005_format_gate` |
| Docker Compose | `backend`, `frontend` y `db` en healthy |
| Bundle del navegador | sin nombres ni valores de secretos ni referencias a módulos de servidor |
| `.env` versionado | no |

## 13. Trazabilidad de los informes (diagnóstico, 2026-10-26)

### Qué queda persistido

| Dato | Dónde | Estado |
|---|---|---|
| Resumen: campaña, canal, objetivo y periodo (texto) | `deterministic_report.input_summary` | Persistido como texto |
| 7 métricas **calculadas** (CTR, CPC, CPL, CPA, ROAS, tasa de conversión, frecuencia) | `deterministic_report.calculated` | Derivadas; no son los importes originales |
| Comparaciones con el periodo anterior: valor actual, valor anterior y variación | `deterministic_report.comparisons` | Solo para las métricas que tienen ambos valores |
| Advertencias de coherencia | `deterministic_report.warnings` | Contienen valores reportados y calculados |
| Texto del informe | `analysis_markdown` | Persistido |
| Huella SHA-256 de la entrada completa (campos, `campaign_id`, versión de prompt) | `input_hash` | **Solo hash**: permite comprobar, no recuperar |

### Qué no queda persistido

- El texto pegado (`rawInput`) y el contexto adicional.
- Los importes originales de las métricas que no tienen periodo anterior comparable. Por ejemplo, la inversión sin periodo anterior no se guarda como número; solo sus derivadas.
- Los valores del periodo anterior sin valor actual.
- El mensaje de usuario que se envió al modelo. Solo se guarda la versión del prompt del sistema, como hash del texto.

### Vínculos

- **Campaña:** `AnalysisCreate` acepta `campaign_id`, pero `analyzeCampaign` **nunca lo envía**. Todo informe generado desde la interfaz queda con `campaign_id` nulo. El nombre de campaña del historial es texto copiado del resumen, no un enlace.
- **Entrada de métricas:** la API no tiene `metric_entry_id`, así que ningún informe queda vinculado a una entrada concreta.

### ¿Podemos reconstruir la entrada de un informe anterior?

**No completamente.** Se puede reconstruir el informe escrito, su resumen, las métricas derivadas y las comparaciones. No se puede reconstruir el texto pegado, los importes originales que no aparecen en las comparaciones, ni el mensaje enviado al modelo. La huella solo permite comprobar que una entrada concreta coincide con la usada.

Los informes creados antes de este diagnóstico tienen la misma limitación.

### Propuesta mínima, compatible y sin cambios de esquema

1. **Guardar la entrada normalizada dentro de `deterministic_report`.** Añadir una clave `input` con `data.model_dump(mode="json")`, y `prompt_version`. Es una columna `jsonb` existente, así que no hay DDL. El trigger de inmutabilidad ya impide cambios posteriores. Coste: el texto pegado queda guardado, con el mismo tratamiento que las entradas de métricas.
2. **Enviar `campaign_id` desde la interfaz** cuando el análisis corresponde a una campaña guardada: se usa el identificador devuelto por `saveCampaign`, y solo mientras el formulario no haya cambiado desde el guardado (comparación de la misma huella en el cliente). La API ya acepta el campo y la clave foránea compuesta ya lo valida.
3. **Vincular a la entrada de métricas** (`metric_entry_id`) como cambio de contrato de la API, sin tocar el esquema: la clave compuesta `(organization_id, campaign_id, metric_entry_id)` ya existe. Requiere que la interfaz guarde la entrada y la reutilice.

Lo que **no** se propone todavía: una tabla nueva `analysis_inputs`. Sería un cambio de esquema y solo se justifica si hay que consultar las entradas por campos.

Pendiente de aprobación: la propuesta 1 afecta a la privacidad de los datos guardados y debe revisarse antes de implementarse.

## 14. Estado de GitHub Actions del PR #3 (2026-10-26)

Los tres jobs (`backend`, `e2e`, `compose`) terminaron en `failure` en 2–3 segundos, con **0 pasos ejecutados** y sin runner asignado.
La anotación del check de GitHub dice literalmente: *«The job was not started because your account is locked due to a billing issue.»*

Este diagnóstico se basa en esa anotación. No se ha ejecutado ningún código del proyecto en GitHub. Las pruebas remotas siguen pendientes hasta que la cuenta esté desbloqueada.

## 15. Snapshot de métricas y vínculo verificado con campañas (2026-10-26)

### Snapshot dentro de `deterministic_report`

Clave nueva `metrics_snapshot`, con la versión de estructura `version: 1`:

```json
"metrics_snapshot": {
  "version": 1,
  "metrics": {"investment": 1000.0, "clicks": 100.0},
  "previous": {"investment": 800.0}
}
```

- `metrics`: las métricas numéricas recibidas por el motor, tal como llegaron (no calculadas): inversión, impresiones,
  alcance, frecuencia, clics, CTR, CPC, leads, CPL, ventas, ingresos y ROAS. Los valores ausentes se omiten.
- `previous`: lo mismo para el periodo anterior, cuando existe.
- **No contiene** `rawInput`, `additionalContext` ni los campos de texto de la campaña. Esos campos siguen existiendo
  solo en `input_summary` (campaña, canal, objetivo y periodo), como antes.
- Se añade a la columna `jsonb` existente. **No hay migración nueva**. El trigger de inmutabilidad protege el contenido.
- Los cálculos (`calculated`), las comparaciones y las advertencias no cambian. Se comprueba que el CPC de 1000 / 100 sigue siendo 10.

**Informes anteriores:** no tienen `metrics_snapshot` y no se rellenan. Se leen igual, sin esa clave. Un informe creado
antes del snapshot no puede reconstruir sus métricas originales más allá de lo que ya contenía.

**Lo que el snapshot no permite:** reconstruir el prompt o la solicitud original. Es deliberadamente parcial por privacidad:
el texto pegado no se guarda automáticamente.

### Vínculo explícito con una entrada guardada

`POST …/analyses` acepta `campaign_id` y `metric_entry_id`. `metric_entry_id` exige `campaign_id` (validación 422).

Verificación en el servidor antes de reclamar nada:
1. La campaña pertenece a la organización (si no, 404 `NOT_FOUND`).
2. La entrada pertenece a esa campaña y a esa organización (si no, 404 `NOT_FOUND`).
3. La entrada guardada coincide con lo que se analiza: texto (`raw_input`), las 12 métricas numéricas y el periodo
   anterior. Si no coincide, **422 `ENTRY_DOES_NOT_MATCH`** y no se crea ningún análisis ni hay llamada al proveedor.

La huella de entrada incluye `metric_entry_id` solo cuando está presente. Las huellas de peticiones sin vínculo no cambian.

### Interfaz

- Al guardar, la interfaz recuerda el identificador de la campaña, el de la entrada y una copia exacta de los datos.
- Al analizar, envía el vínculo **solo si los datos son idénticos** a los guardados. Si el usuario cambió algo, no se envía.
- El servidor verifica igualmente. El vínculo nunca se decide por nombre de campaña ni se asocia a una entrada anterior
  con datos distintos.

### Límites que siguen existiendo

- **Solo se vincula lo guardado desde la interfaz en esa sesión.** Un informe generado sin guardar la campaña antes no tiene vínculo.
- **El texto pegado sí se guarda en la entrada de métricas**, como ya ocurría con el guardado. El snapshot no lo añade.
- **Campañas guardadas antes de esta versión:** no cambian.
- **Análisis anteriores a esta versión:** no tienen vínculo ni snapshot.

### Pruebas

- `test_the_snapshot_keeps_the_metrics_and_never_the_text`: métricas originales y periodo anterior presentes; texto ausente; CPC sin cambios.
- `test_analyses_saved_before_the_snapshot_still_read_without_it`: lectura sin snapshot.
- `test_a_linked_analysis_is_verified_and_stored_with_its_campaign`: vínculo correcto aceptado.
- `test_a_changed_input_is_refused_before_anything_is_claimed`: entrada modificada rechazada, sin llamada ni reclamación.
- `test_an_entry_of_another_organization_is_not_found` y `test_an_entry_of_another_campaign_is_not_found`: aislamiento.
- `test_an_entry_without_its_campaign_is_a_validation_error`: validación.
- `test_a_linked_replay_returns_the_stored_analysis`: idempotencia con vínculo.
- E2E «guardar y analizar la misma campaña»: el vínculo viaja y el servidor lo acepta.

Un defecto que las pruebas detectaron durante el trabajo: `entry_matches` quedó como corrutina por un error al insertar la
función. Al ser siempre verdadera, la comprobación no rechazaba nada. Lo corregí y la prueba de entrada modificada lo cubre.

## 16. Semántica del vínculo y política de reintentos (cierre de casos límite, 2026-10-26)

### Qué representa `metric_entry_id`

`metric_entry_id` representa **la fuente de los datos que se analizaron**, no la solicitud completa. Un vínculo
afirma exactamente lo que el servidor ha comparado, y nada más.

| Campo de la solicitud | Cómo se verifica el vínculo | Dónde está guardado |
|---|---|---|
| Texto de la entrada (`rawInput`) | Igualdad con `raw_input` de la entrada | Entrada de métricas |
| 12 métricas numéricas | Igualdad, con la misma nulabilidad | Entrada de métricas |
| Periodo anterior (`previous`) | Igualdad del JSON | Entrada de métricas |
| Nombre de campaña | Igualdad con el nombre guardado. Si está vacío, el mismo valor por defecto que la interfaz | Campaña |
| Canal, objetivo y periodo textual | Igualdad normalizada (recortado; vacío = nulo) | Campaña |
| **`additionalContext`** | **No se verifica: se rechaza el vínculo si está presente.** No se guarda en ninguna parte. | — |

Si cualquier campo verificable no coincide, o si hay contexto adicional, la respuesta es **422 `ENTRY_DOES_NOT_MATCH`** y
no se reclama nada.

Por tanto, un vínculo **no afirma** que la solicitud sea idéntica a la guardada. Afirma que la entrada y la campaña
guardadas contienen los mismos datos que se analizaron. El registro completo de lo analizado sigue siendo el snapshot
y el resumen del informe, junto con la huella de entrada.

### Qué pasa con los reintentos

- Una solicitud se construye una vez, al pedir el análisis: datos, clave y vínculo (si lo hay). Se guarda tal cual.
- **El reintento reenvía esa misma solicitud.** El cuerpo, el vínculo y la clave no cambian, así que la huella coincide.
- Si la campaña se guarda después del timeout, **no** se añade vínculo al reintento. Un vínculo nuevo cambiaría la huella
  y el servidor devolvería 422 `IDEMPOTENCY_KEY_REUSED` sin cobrar.
- Si el servidor guardó un fallo para esa clave, el reintento usa **una clave nueva** y el mismo cuerpo. El fallo guardado
  se repetiría si se reutilizara la clave.
- Un análisis nuevo (otro clic) crea una solicitud nueva con clave nueva. Solo entonces se usa el vínculo guardado.

### Pruebas

- Backend: campos de texto modificados (nombre, canal, objetivo, periodo), contexto adicional, campaña renombrada
  después de guardar, reintento tras el timeout con la misma clave y cuerpo, y reintento que intenta añadir vínculo (422, sin cobro).
- Unitarias (`src/lib/analysis-request.test.ts`): sin campaña guardada, con la misma entrada, con entrada distinta,
  el escenario de timeout seguido de guardado y reintento, el vínculo conservado en el reintento, y la clave nueva tras un fallo guardado.
- E2E: guardar y analizar la misma campaña (sin contexto adicional, para que el vínculo viaje).

Ningún test llama a OpenRouter. Todas las pruebas usan el transporte simulado o los servidores simulados.

### Lo que sigue sin garantizarse

- `additionalContext` no tiene vínculo verificable. Un análisis con contexto adicional se analiza sin vínculo.
- Una campaña renombrada o editada después de guardarse deja de vincularse. Es el comportamiento correcto: los datos cambiaron.
- Un análisis que se pidió sin vínculo no se vincula después, aunque la campaña se guarde.
