# RCKT Campaign Analyst

Herramienta de análisis ejecutivo de campañas de RCKT, migrada de un prototipo Lovable a una arquitectura con FastAPI.

## Arquitectura

- Frontend: React 19, TypeScript y TanStack Start (SSR).
- Backend: Python 3.12 y FastAPI. Pydantic valida entradas; Python calcula indicadores, prepara el contexto y ejecuta el agente IA.
- Proveedor IA: OpenRouter con modelos principal y de respaldo configurables. El prompt empresarial se conserva en backend/app/prompts/campaign-analyst.txt.
- Despliegue: Docker Compose, dos contenedores separados en un único comando.
- El navegador llama a la Server Function de TanStack, que se comunica con FastAPI en la red interna de Docker. FastAPI no publica puertos ni claves al navegador.
- Sin base de datos, autenticación, RAG ni memoria en esta versión.

## Iniciar

Requisitos: Docker Compose v2.

1. Copiar .env.example a .env y configurar OPENROUTER_API_KEY en el servidor. Nunca subir .env a Git.
2. Ejecutar estos comandos:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 frontend backend
curl --fail http://127.0.0.1:3000/
```

La interfaz queda en http://127.0.0.1:3000 del servidor Docker (cambiar con APP_PORT en .env si ese puerto está ocupado). El puerto 8000 de FastAPI es exclusivamente interno.

La prueba curl confirma disponibilidad HTTP de la interfaz, no una llamada de IA. Probar un análisis real con una clave propia antes de publicar.

## Desarrollo local

Backend:

```bash
cd backend
python -m venv .venv
# Activar entorno virtual
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
python -m uvicorn app.main:app --reload --port 8000
```

Frontend (iniciar con FASTAPI_INTERNAL_URL=http://127.0.0.1:8000 en el entorno del servidor):

```bash
bun install --frozen-lockfile
bun run dev
```

Contrato principal: POST /api/v1/campaigns/analyze, JSON normalizado desde React. Respuesta: JSON con campo analysis (markdown para ExecutiveReportPanel). El endpoint usa Pydantic, motor determinístico y proveedor OpenRouter.

## Seguridad y puesta en producción

El servicio se enlaza a localhost por defecto. Antes de exponer a Internet: configurar HTTPS, autenticación de usuarios, rate limiting y cuotas por usuario, observabilidad y rollback. Sin estos controles, visitantes podrían consumir los créditos de OpenRouter de la empresa.

La llamada directa a OpenRouter desde Node fue eliminada: OpenRouter se invoca únicamente desde FastAPI. La vista previa en Lovable necesita alcanzar un backend FastAPI para ejecutar análisis; el despliegue Docker es autónomo.

Esta PR es preparación de despliegue: no realiza publicación ni fusiona a main automáticamente.
