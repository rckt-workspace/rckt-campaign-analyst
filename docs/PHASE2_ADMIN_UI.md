# Fase 2.6B2C: pantalla administrativa de consumo y auditoría

Estado: implementada en `feat/phase-2-6b2-admin-ui`, apilada sobre `feat/phase-2-6b2-audit-events`. Es una integración
de frontend con los endpoints existentes. No cambia el backend, no crea migraciones y no implementa `platform_admin`.

## 1. Ruta y acceso

- Ruta: `/administracion` (`src/routes/administracion.tsx`).
- Sin sesión: redirección a `/login`. Probado en E2E.
- Con sesión sin membresía `owner` en ninguna organización: la página muestra «Sin acceso de administración» y **no
  pide ningún dato** (ni consumo, ni auditoría). Probado en E2E para analyst y client_viewer.
- Con membresías `owner`: se listan **solo** esas organizaciones. Ningún rol `analyst` o `client_viewer` se ofrece nunca
  como contexto administrativo (`src/lib/admin-access.ts`, con pruebas unitarias).
- El selector se preselecciona con la primera organización owner, y el usuario puede cambiarla. Nunca se preselecciona una
  organización donde el usuario no es owner.
- El enlace **Administración** del encabezado solo aparece a usuarios con alguna membresía owner
  (`AppHeader`, prop `showAdminLink`).

El `organization_id` elegido solo indica el contexto. Antes de cualquier llamada, el servidor de TanStack comprueba que es
una organización owner de la sesión; FastAPI vuelve a comprobar membresía y rol en cada llamada.

## 2. Funciones de servidor

Todas usan `createServerFn`, `currentAccessToken()` y `fastapiRequest()`. Validan la entrada con Zod.

| Función | Llamada a FastAPI | Auditada por FastAPI | Cuándo se invoca |
|---|---|---|---|
| `getAdminOrganizations` | `GET /api/v1/me` | No | Al cargar la ruta |
| `getUsageReport` | `GET /api/v1/organizations/{id}/usage?period=YYYY-MM` | **Sí** (`usage.report_accessed`) | Solo al pulsar «Consultar consumo» |
| `getAuditEvents` | `GET /api/v1/organizations/{id}/audit-events?limit=&cursor=` | No | Al abrir la pantalla, al cambiar de organización, al consultar y al cargar más |

Validaciones: `organizationId` UUID; `period` con el mismo patrón que el backend; `limit` entre 1 y 100; `cursor` entero
positivo. Una entrada inválida devuelve `ADMIN_INVALID_INPUT`, sin mensaje de Zod.

Errores: cada fallo se convierte en un código estable (`src/lib/admin-errors.ts`). El cuerpo de FastAPI nunca llega al
navegador; el cliente FastAPI solo conserva un código estable si el `detail` tiene forma de código (por ejemplo
`AUDIT_UNAVAILABLE`).

## 3. Consumo: lo que se ve y lo que no

- **Análisis** y **llamadas al proveedor** están en secciones separadas, porque no son el mismo número.
- **Tokens y créditos desconocidos** se muestran como «Desconocido» con una etiqueta de estado. Nunca como cero.
- **Créditos**: el importe llega como texto decimal y se muestra tal cual. No se convierte con `Number`, no se pasa a USD
  ni a COP, y el panel dice que no es una factura.
- **Completitud** del coste: `Sin llamadas`, `Ningún coste reportado`, `Parcial` o `Completo`, con el texto correspondiente.
- **Cuota**: plan, límite, unidades consumidas, liberadas, análisis contados, estado del enforcement y reconciliación.
  Las unidades restantes se muestran **solo** cuando el backend declara `remaining_available`; si no, «No disponible».
- **Mes sin actividad**: aviso explícito («Sin actividad registrada en YYYY-MM (UTC)»), no una tabla vacía ni un error.

## 4. Auditoría

- Lista responsiva, en orden recibido (id descendente). No es una tabla horizontal, para no romper el ancho de un teléfono.
- La metadata **no llega al estado de la pantalla**: se descarta en `toAuditPage`.
- Las horas se muestran en UTC con la zona escrita.
- La pantalla no afirma que el id sea el orden de confirmación de las transacciones.
- Paginación por cursor: «Cargar eventos anteriores» pide la página siguiente con `next_cursor`.

## 5. Comportamiento de las consultas

- El informe **no se consulta solo**: no hay polling, ni prefetch, ni reintento automático, ni consulta al cambiar la
  organización o el periodo.
- Cada pulsación de «Consultar consumo» (o «Actualizar consumo») es un acceso auditado. Un doble clic queda bloqueado
  mientras la petición está en vuelo.
- Las respuestas antiguas se descartan: un resultado de otra organización o periodo no sobrescribe el actual.
- Tras una consulta correcta, la primera página de auditoría se vuelve a leer, porque el acceso acaba de quedar registrado.

## 6. Estados visuales

Cargando · sin actividad · sin membresía owner · sin sesión (redirección) · organización no disponible · servicio no
disponible · consumo parcial · registro de auditoría no disponible (el informe no se muestra).

## 7. Pruebas

- **Unitarias** (`src/lib/*.test.ts`, 52 en total, 19 nuevas): transformación de respuestas, cero explícito frente a
  desconocido, precisión decimal como texto, periodos UTC, visibilidad de remanente, completitud no válida, permisos de
  organizaciones, mapeo de errores y mensajes seguros, metadata descartada, orden y cursor, horas en UTC.
- **E2E** (`e2e/admin.spec.ts`, con servicios simulados y base `rckt_e2e_test` preparada con 0008): acceso del owner, acceso
  denegado al analyst y al client_viewer por URL, redirección sin sesión, consulta mensual, costes parciales, mes sin
  actividad, cambio de organización sin consulta, un solo registro de auditoría por pulsación, paginación, navegación de
  vuelta, y viewport de teléfono sin desplazamiento horizontal.
- El simulador de OpenRouter reporta `usage` según un modo por prueba (`none` por defecto, `partial`, `complete`). El
  coste es un valor fijo de prueba.

## 8. Limitaciones y riesgos

1. **No se puede probar una organización ajena desde la interfaz.** El selector solo ofrece organizaciones owner. La
   denegación de una organización ajena está cubierta en el backend y en la función de servidor (`NOT_OWNER`), sin prueba
   E2E dedicada.
2. **Carga de auditoría al cambiar de organización.** Leer la auditoría no es un acceso auditado, pero sí una petición.
   Es una lectura explícita al cambiar de organización, no una consulta de consumo.
3. **Errores de registro obligatorio.** El código `AUDIT_UNAVAILABLE` existe y se mapea, pero no hay prueba E2E que fuerce
   el fallo de la base de datos. La prueba de su mapeo es unitaria.
4. **Sin platform_admin.** Su pantalla y su autorización requieren diseño propio.
5. **Mes de la cuota y reloj.** Pendiente heredado de 2.6B2A y obligatorio antes del piloto: el informe muestra el mes de
   cuota tal como lo fija el backend.
6. **Edición de cuotas, membresías y facturación**: fuera de alcance.
