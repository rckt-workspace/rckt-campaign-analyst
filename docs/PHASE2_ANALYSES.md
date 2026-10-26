# Fase 2.5A: persistencia de análisis y registro de llamadas (diseño revisado)

Estado: **fase 2.5B implementada** (endpoints, historial e interfaz, §11). La migración 0004 está aplicada en la base
local de desarrollo y en las bases de prueba; **no** se ha ejecutado en Lovable Cloud ni en ningún entorno remoto. Base: `feat/phase-2-5-analysis-history`, sobre
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
- **`AI_INVALID_RESPONSE`** añadido a la lista segura de la migración 0004 (no aplicada en remoto: se corrigió en
  origen, no en una migración nueva).
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
