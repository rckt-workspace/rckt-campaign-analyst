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

No existe ningún endpoint que modifique o elimine una entrada de métricas, ni que elimine una campaña.

## 2. Roles

Se siguió la instrucción de la fase, que difiere del diseño original (`PHASE2_DESIGN.md`, sección 4):
el diseño permitía a `analyst` crear **o editar** campañas; aquí `analyst` solo crea y registra métricas,
y editar o archivar queda reservado a `owner`. **Pendiente de confirmar** por el responsable de RCKT.

Constantes en `backend/app/domain/access.py`: `VIEW_ROLES`, `RECORD_CAMPAIGN_ROLES`, `CAMPAIGN_ADMIN_ROLES`.

## 3. Reglas

- **Acceso:** `require_role` valida membresía y rol en cada operación. Una campaña de otra organización responde
  `404 NOT_FOUND`, igual que una inexistente. Un `organization_id` en el cuerpo responde `422`.
- **Archivado:** una campaña archivada no admite edición (`409 CAMPAIGN_ARCHIVED`) ni nuevas entradas de métricas.
  Su historial sigue siendo legible.
- **Inmutabilidad:** la aplicación no tiene `UPDATE` ni `DELETE` sobre `campaign_metric_entries`. El trigger de
  PostgreSQL lo impide aunque se ejecute SQL directo. Las correcciones son entradas nuevas.
- **Periodo:** `period_end` no puede ser anterior a `period_start`. En PATCH se valida el resultado combinado
  (`422 INVALID_PERIOD`).
- **Valores:** los importes respetan `numeric(14,2)`, los costos unitarios y ratios `numeric(14,4)` o `(12,4)`, y
  los conteos son enteros no negativos (`bigint`). Un valor que la base no podría guardar se rechaza con `422`
  antes del INSERT.

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

El guardado envía dos peticiones: crea la campaña y luego registra su primera entrada de métricas. Los decimales se
redondean a la escala de la columna. Los conteos fraccionarios no se redondean: llegan como `422` y la interfaz
muestra un aviso.

## 6. Riesgos y pendientes

- **Guardado no atómico:** si falla la segunda petición, queda una campaña sin métricas. Solución propuesta: un
  endpoint compuesto que haga ambas inserciones en una transacción. Pendiente.
- **Sin historial de métricas en la interfaz:** la API lo expone (`GET .../metrics`), la interfaz solo lista campañas.
- **Lista sin paginación en la interfaz:** muestra las 50 campañas más recientes.
- **Edición y archivado sin interfaz:** solo disponibles por API.
- **Sin políticas RLS:** las tablas mantienen RLS activado sin políticas, como en la migración existente. El acceso
  pasa por FastAPI con la membresía verificada.
- **Lovable Cloud:** no se ejecutó SQL ni migraciones. No hay cambios de esquema pendientes de revisión.
