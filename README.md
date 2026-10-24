# RCKT Campaign Analyst

Herramienta de análisis ejecutivo de campañas digitales desarrollada en RCKT. Permite ingresar métricas mediante texto libre o formulario rápido y obtener hallazgos y acciones recomendadas, explicados para usuarios no técnicos.

## Arquitectura

- **Frontend:** React 19, TypeScript, TanStack Start, Tailwind.
- **Backend:** server functions y renderizado del lado del servidor dentro de TanStack Start.
- **Cálculos:** motor determinístico que normaliza métricas, calcula indicadores derivados y alerta sobre inconsistencias.
- **Agente de IA:** un único analista, integrado con OpenRouter desde el servidor (modelo principal y fallback configurables).
- **Despliegue:** Vite + Nitro (preset Node.js) en **un contenedor** operado mediante Docker Compose.
- **Sin base de datos ni autenticación** en esta versión.

Flujo: formulario o texto → normalización de métricas → validaciones y cálculos → server function → OpenRouter → informe ejecutivo.

No es necesario levantar por separado un contenedor de frontend y otro de backend: TanStack Start produce un solo servidor que atiende tanto la interfaz como las llamadas de IA.

## Requisitos

- Docker Engine con Docker Compose v2 (servidor Linux o equipo de desarrollo).
- Acceso de red a OpenRouter y una clave válida.
- Un dominio, HTTPS y control de acceso si va a exponerse fuera del servidor.

## Levantar en Docker Compose

```bash
cp .env.example .env
```

Edite **.env** y configure `OPENROUTER_API_KEY` con su clave real. Puede cambiar `CHAT_PRIMARY_LLM` y `CHAT_FALLBACK_LLM` sin reconstruir la imagen. No use variables `VITE_*` para secretos ni suba el archivo `.env` a Git.

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app
```

La aplicación responderá en **http://127.0.0.1:3000** desde el mismo equipo, por defecto. El contenedor atiende en el puerto 3000 y Docker publica el puerto indicado por `APP_PORT`.

Verificación básica de disponibilidad (no consume solicitudes de IA):

```bash
curl --fail --silent --show-error http://127.0.0.1:3000/ > /dev/null
docker compose ps
```

Para actualizar con nuevos cambios del repositorio:

```bash
git pull --ff-only
docker compose up -d --build
```

Para detener el servicio:

```bash
docker compose down
```

## Desarrollo sin Docker

```bash
bun install --frozen-lockfile
bun run dev
```

Compilación para un servidor Node fuera de Lovable:

```bash
RCKT_DEPLOY_TARGET=node bun run build
node .output/server/index.mjs
```

En PowerShell, defina primero `$env:RCKT_DEPLOY_TARGET="node"`. El script `bun run start` inicia la misma salida generada por Nitro.

El ajuste condicional en `vite.config.ts` preserva el build y la vista previa gestionados por Lovable, y selecciona el preset de Node exclusivamente cuando `RCKT_DEPLOY_TARGET=node`.

## Checklist antes de hacerlo público

1. Validar compilación, inicio de servidor, carga de interfaz y una llamada real de prueba a OpenRouter.
2. Seleccionar un modelo con disponibilidad y límites apropiados: los modelos gratuitos pueden presentar restricciones y no garantizan capacidad de producción.
3. Agregar **autenticación y límites de uso por usuario/IP** antes de permitir que visitantes externos ejecuten análisis (la aplicación actual no incorpora estas protecciones).
4. Publicar mediante un proxy inverso con **HTTPS**, política de acceso y registros sin información sensible; mantener `APP_BIND_IP=127.0.0.1` si el proxy corre en el mismo servidor.
5. Configurar alertas, respaldos de configuración y un mecanismo de reversión de versión. Como no hay base de datos, no se requieren volúmenes de persistencia en esta etapa.

**Seguridad:** exponer `APP_BIND_IP=0.0.0.0` sin autenticación ni controles permitiría a terceros consumir la cuota o créditos de OpenRouter asociados a la clave de la empresa. No hacerlo en producción.

## Estado de esta entrega

Esta rama prepara el despliegue reproducible y la validación de arranque; no publica automáticamente el proyecto en un dominio ni configura credenciales en servidores externos.
