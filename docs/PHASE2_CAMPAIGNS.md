# Fase 2.4: campañas y entradas de métricas

Estado: implementado en la rama `feat/phase-2-4-campaign-persistence`. Sin migraciones nuevas: el esquema de
`20261025000200_campaigns.sql` (Alembic `0003_campaigns`) cubre todos los requisitos.

## 1. Endpoints

Prefijo: `/api/v1/organizations/{organization_id}/campaigns`. La organización viene siempre de la ruta.

| Método | Ruta | Roles | Respuesta correcta |
|---|---|---|---|
| POST | `` | owner, analyst | 201 `CampaignRead` |
| GET | `` | owner, analyst, client_viewer | 200 lista (`include_archived`, `limit` 1–200, `offset`) |
| GET | `/{campaign_id}` | owner, analyst, client_viewer | 200 `CampaignRead` |
| PATCH | `/{campaign_id}` | owner | 200 `CampaignRead` (solo campos enviados) |
| POST | `/{campaign_id}/archive` | owner | 200 `CampaignRead` (idempotente) |
| POST | `/{campaign_id}/metrics` | owner, analyst | 201 `CampaignMetricRead` |
| GET | `/{campaign_id}/metrics` | owner, analyst, client_viewer | 200 lista, más recientes primero |
| POST | `/with-metrics` | owner, analyst | 201 `CampaignWithMetricsRead` (campaña y primera entrada, en una transacción) |

No existe ningún endpoint que modifique o elimine una entrada de métricas, ni que elimine una campaña.

`POST …/with-metrics` recibe `{ "campaign": {…}, "metrics": {…} }`. Los endpoints individuales se mantienen sin cambios.

## 2. Roles

Decisión definitiva de RCKT (cierre de la fase 2.4). `PHASE2_DESIGN.md`, sección 4, usa la misma matriz:

- **owner:** crear, consultar, editar y archivar campañas; registrar y consultar métricas.
- **analyst:** crear y consultar campañas; registrar y consultar métricas. No puede editar ni archivar.
- **client_viewer:** solo consultar campañas y su historial de métricas.

Constantes en `backend/app/domain/access.py`: `VIEW_ROLES`, `RECORD_CAMPAIGN_ROLES`, `CAMPAIGN_ADMIN_ROLES`.
`tests/test_campaign_roles.py` comprueba cada rol contra cada operación, y el comportamiento sobre campañas archivadas.

## 3. Reglas

- **Acceso:** `require_role` valida membresía y rol en cada operación. Una campaña de otra organización responde
  `404 NOT_FOUND`, igual que una inexistente. Un `organization_id` en el cuerpo responde `422`.
- **Archivado:** una campaña archivada no admite edición (`409 CAMPAIGN_ARCHIVED`) ni nuevas entradas de métricas.
  Su historial sigue siendo legible.
- **Inmutabilidad:** la aplicación no tiene `UPDATE` ni `DELETE` sobre `campaign_metric_entries`. El trigger de
  PostgreSQL lo impide aunque se ejecute SQL directo. Las correcciones son entradas nuevas.
- **Periodo:** `period_end` no puede ser anterior a `period_start`. En PATCH se valida el resultado combinado
  (`422 INVALID_PERIOD`).
- **Precisión:** cada valor se guarda exactamente como se envió, o se rechaza con `422`. Nunca se redondea en silencio.
  Límites según las columnas de PostgreSQL:

  | Campos | Tipo | Dígitos y decimales admitidos |
  |---|---|---|
  | `investment`, `revenue` | `numeric(14,2)` | hasta 12 enteros y 2 decimales |
  | `cpc`, `cpl` | `numeric(14,4)` | hasta 10 enteros y 4 decimales |
  | `frequency`, `roas` | `numeric(12,4)` | hasta 8 enteros y 4 decimales |
  | `ctr` | `numeric(9,4)` | hasta 5 enteros y 4 decimales |
  | `impressions`, `reach`, `clicks`, `leads`, `conversions` | `bigint` | enteros no negativos |

- **Normalización documentada:** el número JSON se convierte a `Decimal` a partir de su representación textual
  (`0.3` → `0.3`), sin pasar por `float`. Al devolverse, los importes son números JSON. `previous` se guarda en
  `jsonb` como números JSON, sin restricción de precisión en la base.
- **Interfaz:** envía los valores tal como el usuario los escribió. Si uno excede la escala de su columna, el guardado
  no se realiza y el aviso lo indica. No hay redondeo en el navegador.

## 4. Errores

Códigos estables en `detail`, sin texto de la base ni trazas: `NOT_FOUND` (404), `FORBIDDEN` (403),
`AUTHENTICATION_REQUIRED` y `INVALID_TOKEN` (401), `CAMPAIGN_ARCHIVED` (409), `INVALID_PERIOD` (422). Las
validaciones de Pydantic devuelven el formato estándar de FastAPI con `422`.

## 5. Frontend

Archivos:
- `src/lib/campaign-session.server.ts`: sesión y organización desde la cookie HttpOnly. Solo servidor.
- `src/lib/campaign-api.functions.ts`: `saveCampaign` (POST) y `listSavedCampaigns` (GET). `analyzeCampaign` usa
  ahora los mismos helpers de sesión, sin cambio de comportamiento.
- `src/components/campaign/CampaignInputPanel.tsx`: botón «Guardar campaña» junto a «Analizar campaña».
- `src/components/campaign/SavedCampaignsPanel.tsx`: lista de campañas de la organización.
- `src/routes/index.tsx`: conecta guardado, lista y sesión caducada (redirige a `/login`, como el análisis).

El guardado envía **una sola petición** a `POST …/with-metrics`. La API crea la campaña y su primera entrada en
una transacción: si cualquiera de las dos inserciones falla, PostgreSQL revierte ambas. Los valores no se redondean
en el navegador; si alguno no cabe en su columna, la API responde `422 CAMPAIGN_INVALID` y la interfaz lo indica.

## 6. Riesgos y pendientes

- **Sin historial de métricas en la interfaz:** la API lo expone (`GET .../metrics`), la interfaz solo lista campañas.
- **Lista sin paginación en la interfaz:** muestra las 50 campañas más recientes.
- **Edición y archivado sin interfaz:** solo disponibles por API.
- **Sin políticas RLS:** las tablas mantienen RLS activado sin políticas, como en la migración existente. El acceso
  pasa por FastAPI con la membresía verificada.
- **Lovable Cloud:** no se ejecutó SQL ni migraciones. No hay cambios de esquema pendientes de revisión.


## 7. Interpretación numérica (texto pegado, formulario y periodo anterior)

Implementada en `src/lib/metric-number.ts`, usada por `src/lib/campaign-metrics.ts` para los tres orígenes de datos.
Nunca se sustituye un valor inválido por cero, null ni otro número. Un valor no legible queda como incidencia con su
texto original; mientras existan incidencias, la interfaz no analiza ni guarda.

Reglas:
- **Dos separadores:** el último es el decimal y el otro debe agrupar miles en bloques de tres (`1.234,56`, `1,234.56`).
- **Un separador seguido de dos cifras** es decimal (`2,47`, `2.10%`).
- **Un separador seguido de tres cifras** es ambiguo (`1234,567`, `1,234`, `4.850`), salvo dos casos en los que
  la otra lectura es imposible: un conteo no admite decimales, y un importe con `$` o `COP` agrupa miles con punto
  (`$9.291`, `COP 1.234`). Ninguna lectura alternativa sin esas condiciones.
- **Símbolos** (`$`, `COP`, `€`, `USD`, `EUR`) y `%` se ignoran. Espacios dentro del número no se aceptan.
- **Precisión por tipo de métrica**, igual que las columnas de PostgreSQL: importes 12 enteros y 2 decimales; costos
  unitarios 10 y 4; ratios 8 y 4; porcentaje 5 y 4; conteos enteros de hasta 15 cifras.
- **Exactitud:** el número debe volver a imprimirse con los mismos dígitos. Un valor que `Number` no representaría
  exactamente se rechaza.
- **Negativos y malformados** se rechazan.

Motivos de incidencia: `ambiguous`, `too_many_decimals`, `too_large`, `fractional_count`, `negative`, `malformed`.
Cada aviso indica el campo (incluido «(periodo anterior)»), el texto original y qué corregir.

Pruebas: `src/lib/metric-number.test.ts` y `src/lib/campaign-metrics.test.ts` (ejecutar con `npm run test:unit`,
sin dependencias nuevas: usa `node:test` y `esbuild`, ya incluido por Vite), y `e2e/number-parsing.spec.ts`.
