# Fase 2: arquitectura empresarial y persistencia (diseño)

Estado: **propuesta para revisión**. No hay código de Fase 2 implementado. Fase 1 queda como base (commits `e537d03` y `a86f792`).

## 1. Objetivo y alcance

Convertir el MVP en una plataforma multi-cliente donde RCKT puede ofrecer análisis de campañas a distintas organizaciones (clientes), con datos aislados, usuarios con roles, historial de análisis, cuotas y auditoría.

Fuera de alcance de esta fase: integraciones con Meta/Google Ads, RAG, multiagente, dashboards de Fase 3 y facturación automática.

Restricciones que se mantienen:
- FastAPI es el backend principal. React + TanStack Start es el frontend. Docker Compose orquesta todo.
- OpenRouter se invoca solo desde FastAPI. Las claves no llegan al navegador.
- El diseño visual no cambia.

## 2. Decisiones propuestas (requieren confirmación)

| Tema | Propuesta | Alternativa descartada y motivo |
|---|---|---|
| Base de datos | PostgreSQL: contenedor en desarrollo (Compose, sin puerto publicado) y **PostgreSQL gestionado en producción** | SQLite: no soporta bien concurrencia ni RLS. |
| Acceso a datos | SQLAlchemy 2.x (ORM tipado) + psycopg 3 | Consultas SQL crudas: más riesgo de fugas entre tenants. |
| Migraciones | Alembic, versionadas en `backend/migrations/`, ejecutadas por un servicio `migrate` de un solo uso | `create_all` al arrancar: no versionado, no reversible. |
| Autenticación (piloto) | **Proveedor de autenticación gestionado.** La aplicación no almacena contraseñas. Guarda solo el identificador estable del proveedor (`external_subject`) | Contraseñas y sesiones propias: riesgo de seguridad que no aporta valor en el piloto. |
| Autenticación (objetivo) | SSO corporativo (OIDC) en una etapa posterior, sobre el mismo proveedor | Implementar IdP propio: costo de seguridad alto. |
| Aislamiento de clientes | Modelo de base compartida con `organization_id` en cada fila de negocio + verificación en cada consulta; RLS de Postgres como defensa adicional (etapa 2.7) | Base por cliente: operación costosa y migraciones multiplicadas. |
| Cuotas | Límites de consumo de IA **configurables por organización** y mes, reservados **antes** de llamar al proveedor | Medir después: permite gasto sin control. |
| Idempotencia de análisis | Cabecera `Idempotency-Key` en el POST de análisis | Sin ella, un reintento del navegador cobra dos veces. |

## 3. Modelo de datos

Convenciones: claves primarias `uuid` generadas por la aplicación; timestamps `timestamptz` en UTC; montos en `numeric(14,2)` o enteros en micro-unidades; `citext` para correos. Todas las tablas de negocio tienen `organization_id` no nulo.

### 3.1 Identidad y tenancy

**organizations**
- `id`, `name`, `slug` (único), `status` (`active`, `suspended`), `created_at`, `updated_at`.
- Representa a un cliente de RCKT. RCKT interno también es una organización.

**users**
- `id`, `email` (único, `citext`), `full_name`, `password_hash` (nulo si entra solo por SSO), `is_active`, `is_platform_admin` (solo personal de RCKT para soporte), `last_login_at`, `created_at`.

**memberships**
- `id`, `organization_id` → organizations, `user_id` → users, `role` (`owner`, `analyst`, `client_viewer`), `created_at`.
- Único en `(organization_id, user_id)`. Un usuario puede pertenecer a varias organizaciones (por ejemplo, un consultor).

**invitations**
- `id`, `organization_id`, `email`, `role`, `token_hash` (SHA-256 del token, nunca el token), `invited_by`, `expires_at`, `accepted_at`, `revoked_at`.

**sessions** (solo si se usa cookie de sesión propia; con proveedor gestionado no se crea)
- `id`, `user_id`, `token_hash`, `active_organization_id` (nullable), `created_at`, `last_seen_at`, `expires_at`, `revoked_at`, `ip_address`, `user_agent`.

### 3.2 Campañas y datos de entrada

**campaigns**
- `id`, `organization_id`, `name`, `channel`, `objective`, `currency` (por defecto `COP`), `period_start`, `period_end`, `created_by`, `archived_at`, `created_at`, `updated_at`.

**campaign_metric_entries**
- `id`, `organization_id`, `campaign_id`, `period_start`, `period_end`, `source` (`form`, `text`), `raw_input` (texto original, con límite de 12.000 caracteres), `investment`, `impressions`, `reach`, `frequency`, `clicks`, `ctr`, `cpc`, `leads`, `cpl`, `conversions`, `revenue`, `roas`, `previous_metrics` (jsonb, mismo contrato que `PreviousMetrics`), `created_by`, `created_at`.
- Las métricas van en columnas tipadas para poder consultarlas en Fase 3. `previous_metrics` queda en jsonb porque es un contrato de entrada.
- Principio: las entradas son inmutables. Una corrección crea una entrada nueva.

### 3.3 Análisis de IA e historial

**analyses**
- `id`, `organization_id`, `campaign_id` (nullable para análisis sin campaña guardada), `metric_entry_id` (nullable), `requested_by`, `idempotency_key` (único por organización), `status` (`running`, `succeeded`, `failed`), `input_hash` (SHA-256 de la entrada normalizada), `deterministic_report` (jsonb: métricas calculadas, comparaciones y advertencias de `metrics.py`), `analysis_markdown` (texto), `prompt_version`, `model_requested`, `model_used`, `used_fallback`, `error_code` (códigos seguros como `AI_TIMEOUT`, nunca el cuerpo del proveedor), `latency_ms`, `created_at`, `completed_at`.
- Índices: `(organization_id, created_at desc)`, `(organization_id, campaign_id, created_at desc)`.
- Un análisis fallido también se guarda: sirve para soporte y para cuotas.

**analysis_usage** (un registro por llamada al proveedor, incluido el fallback)
- `id`, `organization_id`, `analysis_id`, `model`, `attempt` (`primary`, `fallback`), `prompt_tokens`, `completion_tokens`, `cost_micros` (costo en micro-USD), `status`, `created_at`.
- Los tokens y el costo deben leerse de la respuesta de OpenRouter. **Pendiente de verificar** el formato exacto del campo `usage` con una llamada autorizada.

### 3.4 Cuotas, costos y auditoría

**organization_quotas**
- `organization_id` (pk), `plan` (`pilot`, `standard`, `enterprise`), `monthly_analysis_limit`, `monthly_cost_limit_micros`, `updated_by`, `updated_at`.

**usage_monthly** (resumen para consultas rápidas; se recalcula desde `analysis_usage`)
- `organization_id`, `year_month`, `analyses_count`, `tokens_total`, `cost_micros`. Clave primaria `(organization_id, year_month)`.

**audit_events** (solo inserción)
- `id` bigint identidad, `organization_id` (nullable para eventos de plataforma), `actor_user_id`, `action` (por ejemplo `auth.login`, `member.role_changed`, `analysis.created`, `quota.updated`), `entity_type`, `entity_id`, `ip_address`, `metadata` (jsonb sin secretos), `created_at`.
- Permisos de base: la aplicación solo tiene `INSERT` y `SELECT` sobre esta tabla.

### 3.5 Relaciones resumidas

```
organizations 1─* memberships *─1 users
organizations 1─* invitations, sessions (vía users), campaigns, analyses
campaigns 1─* campaign_metric_entries 1─* analyses
analyses 1─* analysis_usage
organizations 1─1 organization_quotas
organizations 1─* usage_monthly, audit_events
```

## 4. Roles y permisos

| Acción | owner | analyst | client_viewer | platform_admin (RCKT) |
|---|---|---|---|---|
| Ver campañas e historial de su organización | ✅ | ✅ | ✅ | ✅ (soporte, con auditoría) |
| Crear o editar campañas y entradas de métricas | ✅ | ✅ | ❌ | ❌ |
| Ejecutar análisis de IA | ✅ | ✅ | ❌ | ❌ |
| Invitar miembros y cambiar roles | ✅ | ❌ | ❌ | ✅ |
| Ver uso, costos y auditoría de su organización | ✅ | ❌ | ❌ | ✅ |
| Crear organizaciones, cambiar cuotas, suspender | ❌ | ❌ | ❌ | ✅ |

Regla: `client_viewer` ve los análisis guardados y no puede generar costos. Cualquier endpoint que consuma el proveedor exige `owner` o `analyst`.

## 5. Endpoints (prefijo `/api/v1`)

Autenticación
- `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/password` (cambio de contraseña).

Organizaciones y miembros
- `GET /organizations` (las del usuario).
- `POST /organizations` (solo platform_admin).
- `GET|PATCH /organizations/{org_id}`.
- `GET /organizations/{org_id}/members`, `PATCH|DELETE /organizations/{org_id}/members/{user_id}`.
- `POST /organizations/{org_id}/invitations`, `POST /invitations/{token}/accept`, `DELETE /organizations/{org_id}/invitations/{id}`.

Campañas
- `GET|POST /organizations/{org_id}/campaigns`.
- `GET|PATCH /organizations/{org_id}/campaigns/{campaign_id}`, `POST .../archive`.
- `GET|POST /organizations/{org_id}/campaigns/{campaign_id}/metrics`.

Análisis
- `POST /organizations/{org_id}/analyses` con `Idempotency-Key`. Acepta `campaign_id` opcional o un bloque de métricas sin guardar.
- `GET /organizations/{org_id}/analyses?campaign_id=&cursor=`, `GET /organizations/{org_id}/analyses/{analysis_id}`.
- El endpoint actual `POST /api/v1/campaigns/analyze` se conserva **sin cambios de contrato** durante la transición, detrás de autenticación, para que la UI actual no se rompa.

Uso, cuotas y auditoría
- `GET /organizations/{org_id}/usage?period=2026-10`.
- `GET|PUT /organizations/{org_id}/quota` (solo platform_admin).
- `GET /organizations/{org_id}/audit-events?cursor=` (owner y platform_admin).

Salud
- `GET /healthz` (vive, sin dependencias) y `GET /readyz` (verifica la base de datos).

Respuestas de error: códigos estables (`AUTH_REQUIRED`, `FORBIDDEN`, `NOT_FOUND`, `QUOTA_EXCEEDED`, `AI_UNAVAILABLE`). `NOT_FOUND` se usa también cuando el recurso existe en otra organización, para no revelar su existencia.

## 6. Reglas de seguridad

- **Tenancy:** el `org_id` solo viene de la ruta y se valida contra la membresía de la sesión. Nunca se toma del cuerpo de la petición. Toda consulta de negocio recibe `organization_id` como parámetro obligatorio.
- **Identidad:** la autenticación la gestiona el proveedor. FastAPI valida el token de acceso (firma, emisor, audiencia y expiración) y no acepta tokens de otros emisores. La aplicación no guarda contraseñas.
- **Sesiones:** la gestiona el proveedor. Si se usa cookie de sesión propia, debe ser `HttpOnly`, `SameSite=Lax` y `Secure` en HTTPS.
- **CSRF:** en los métodos que modifican datos se valida el encabezado `Origin`.
- **Límites:** tamaños máximos ya existentes en Pydantic. Los límites de intentos de login quedan en el proveedor de autenticación.
- **Secretos:** `OPENROUTER_API_KEY`, contraseña de Postgres y claves de sesión solo en variables de entorno del servidor. Ninguno en logs, respuestas ni auditoría.
- **Bootstrap:** no hay registro público. El primer `platform_admin` se crea con un comando CLI en el servidor, sobre un `app_users` ya existente.
- **Base de datos:** rol de aplicación con permisos mínimos; rol de migración separado; la tabla de auditoría no admite `UPDATE` ni `DELETE`.
- **Red:** Postgres solo en la red interna de Compose. FastAPI tampoco publica puerto.
- **Datos sensibles:** montos e ingresos de clientes se tratan como confidenciales. Política de retención por definir (ver sección 9).

## 7. Flujo de un análisis con cuota e idempotencia

1. Validar sesión, membresía con rol `owner` o `analyst`, y estado de la organización.
2. Buscar `Idempotency-Key`. Si ya existe un análisis completado, devolverlo sin cobrar. Si está en `running`, responder 409.
3. Verificar cuota y **reservar** la llamada en `analysis_usage` con estado `reserved`.
4. Calcular métricas en Python (`metrics.py`) y guardar `deterministic_report`.
5. Llamar a OpenRouter (primario, luego fallback si aplica) con timeout explícito.
6. Actualizar `analysis_usage` con tokens y costo reales, y `analyses` a `succeeded` o `failed`.
7. Registrar el evento en `audit_events`.

Si el paso 5 falla, la reserva se libera y el análisis queda `failed` con código seguro.

## 8. Plan de implementación por etapas (actualizado)

Cada etapa termina con pruebas, Compose funcionando y sin cambios en el comportamiento visible del MVP.

| Etapa | Contenido | Criterio de salida |
|---|---|---|
| **2.0** | Aprobación de este diseño y de las decisiones de la sección 2. | Decisiones confirmadas por escrito. |
| **2.1** Persistencia base | Servicio `db` en Compose, SQLAlchemy, Alembic con migración inicial de `organizations`, `users`, `memberships`. Servicio `migrate`. `/readyz`. Pruebas contra Postgres real en CI. | `docker compose up` migra y arranca. Pruebas de migración hacia arriba y abajo. |
| **2.2** Autenticación gestionada | Integración del proveedor de autenticación: validación de tokens en FastAPI, vinculación con `app_users`, cierre de sesión, comando CLI de bootstrap del primer `platform_admin`. | Tokens inválidos, expirados o de otro emisor rechazados. Ninguna contraseña en la base de datos. |
| **2.3** Tenancy y roles | Organizaciones, miembros, invitaciones, guardas de permiso. Selector de organización en la interfaz. | Pruebas de acceso cruzado (IDOR) en cada endpoint: un usuario de A nunca ve ni modifica datos de B. |
| **2.4** Campañas | Campañas y entradas de métricas guardadas, con autorización por organización desde la primera operación. | Entradas inmutables. Pruebas de IDOR y de rol `client_viewer` de solo lectura. |
| **2.5** Historial de análisis | Persistir análisis, idempotencia y `analysis_usage`. Pantalla de historial. El endpoint MVP se mantiene. `client_viewer` no puede iniciar análisis con costo. | Un reintento con la misma clave no genera un segundo cargo. Prueba con proveedor simulado. |
| **2.6** Cuotas, costos y auditoría | Reserva de cuota, `usage_monthly`, `audit_events`, endpoints de uso y auditoría. | Con cuota agotada no se llama al proveedor (prueba con simulado). |
| **2.7** Endurecimiento | RLS de Postgres como segunda barrera, copias de seguridad probadas, métricas de observabilidad, límites de tasa en el proxy. | Restauración de copia verificada. RLS activo con pruebas. |
| **2.8** SSO (opcional) | OIDC con Microsoft Entra o Google Workspace. | Inicio de sesión corporativo sin contraseñas locales para esa organización. |

Orden recomendado (actualizado): 2.1 → 2.2 → 2.3 → **2.4 campañas → 2.5 historial** → 2.6 → 2.7 → 2.8. La gestión de campañas va antes que el historial, porque el historial depende de campañas y entradas de métricas guardadas.

## 9. Preguntas para decidir antes de 2.1

1. ¿Qué proveedor de autenticación gestionada se usará en el piloto? (Pendiente de elegir; afecta la integración de 2.2.)
2. ¿Qué personal de RCKT necesita acceso de soporte a datos de clientes, y con qué auditoría?
3. ¿Cuál es la retención esperada de textos de campaña y de análisis? ¿Hay requisitos contractuales de los clientes?
4. Confirmado: las instrucciones internas del agente (prompt) viven solo en el backend. Los clientes no las ven.
5. ¿Cuáles son los límites de consumo de IA por organización para el piloto? (Configurables; los valores iniciales se definen en 2.6.)
6. Confirmado: PostgreSQL gestionado en producción. Pendiente: proveedor y política de copias de seguridad.

## 10. Riesgos

- **Fuga entre clientes:** el riesgo principal. Mitigación: `organization_id` obligatorio, pruebas de IDOR desde 2.3 y RLS en 2.7.
- **Costos descontrolados:** mitigación con reserva de cuota antes de cada llamada.
- **Dependencia del formato `usage` de OpenRouter:** verificarlo con una llamada autorizada antes de 2.5.
- **Complejidad de Lovable:** la vista previa de Lovable necesita un backend alcanzable. Los cambios de autenticación afectan la vista previa; documentar cómo se prueba.
- **Migraciones con datos reales:** cada migración se prueba hacia arriba y hacia abajo en CI antes de llegar a producción.
