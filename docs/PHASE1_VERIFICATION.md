# Fase 1: verificación de estabilización

Fecha de verificación: 24/oct/2026. Rama: `chore/docker-compose-node-production`. PR #1 en borrador.

## Resultados locales

| Verificación | Resultado |
|---|---|
| `python -m pytest -q tests` (backend, Python 3.11 local) | 26 passed |
| `python -m pytest -q tests` (backend, Python 3.12 en `python:3.12-slim`) | 26 passed |
| `bunx tsc --noEmit` | exit 0 |
| `bun run build:node` (servidor Node standalone) | OK, genera `.output/server/index.mjs` |
| `bun run test:e2e` (Playwright, Chromium, proveedor simulado) | 8 passed |
| `docker compose config --quiet` (con y sin `.env`) | OK |
| `docker compose build` | OK, ambas imágenes |
| `docker compose up -d --no-build` con `APP_PORT=3100` | Backend `healthy`. `/healthz` 200 dentro de la red. Frontend 200 en `127.0.0.1:3100`. Backend sin puerto publicado. |

## Pruebas E2E (`e2e/campaign.spec.ts`)

Ejecutadas contra el servidor Node compilado, FastAPI real y un proveedor OpenRouter simulado (`e2e/fake-openrouter.py`) que no consume créditos.

1. Carga inicial de React: título, pestañas y botón deshabilitado sin datos.
2. Texto libre: el informe llega desde FastAPI con sus secciones, y se hace una sola llamada al proveedor.
3. Formulario rápido: el ejemplo se analiza con un único informe.
4. Estado loading: se muestra mientras FastAPI consulta al proveedor.
5. Error del proveedor: estado de error, dos intentos (primario y fallback), y el detalle del proveedor no llega al navegador.
6. Reintento: una sola llamada y luego el informe.
7. Doble clic en Analizar: una sola solicitud.
8. Ausencia de credenciales: ni la clave de prueba ni direcciones internas aparecen en el HTML ni en la respuesta de la server function.

## Validación de formato

`backend/tests/test_analysis_flow.py` valida con `e2e/fixtures/sample-report.md` que el informe tiene entre 250 y 450 palabras y contiene las secciones obligatorias. Otra prueba confirma que una respuesta truncada (`finish_reason=length`) se devuelve para que el guardia de formato decida.

## Modelos (catálogo público de OpenRouter, sin inferencias)

Consulta a `GET https://openrouter.ai/api/v1/models` el 24/oct/2026. No se usó clave.

- `google/gemma-3-4b-it:free`: **ya no aparece** en el catálogo. Se retiró como valor por defecto.
- `google/gemma-3-27b-it`: listado, de pago, precio bajo, sin razonamiento oculto. **Principal por defecto.**
- `google/gemma-4-31b-it:free` y `google/gemma-4-26b-a4b-it:free`: gratuitos, pero soportan razonamiento que consume `max_tokens`. Alternativa que requiere una prueba autorizada antes de usarla.
- `openrouter/free`: router gratuito listado. **Fallback por defecto.**

Un modelo listado en el catálogo no garantiza disponibilidad para la cuenta. Hay que confirmarla con una llamada autorizada antes de publicar.

`CHAT_MAX_TOKENS=1200`. Estimación: un informe de 250–450 palabras en español con markdown necesita unos 700–900 tokens. Con 450 se cortaba.

## Problema de GitHub Actions (facturación)

Los runs del PR #1 (`40123873807` y anteriores) fallan en ~3 segundos. Las anotaciones indican: *"The job was not started because your account is locked due to a billing issue."* Es un bloqueo de la cuenta, no un fallo del código. **No se puede verificar el CI hasta que se resuelva la facturación.**

Cambio en el workflow: los runners pasan de `ubuntu-latest` a `ubuntu-24.04`, porque `ubuntu-latest` migra a Ubuntu 26 en octubre de 2026. Se agregó un job `e2e`. Ese job **no se ha ejecutado en GitHub**.

## Seguridad

- Ningún `.env` está versionado. El archivo `.env` local usado para las pruebas fue eliminado.
- Búsqueda de claves con formato `sk-or-` o `Bearer <token>` en el repositorio: sin coincidencias.
- Las imágenes no contienen `.env`. Los contextos de build lo excluyen.
- FastAPI no publica puerto. Solo es accesible desde la red de Docker.
- Los errores del proveedor no se exponen al navegador ni a los logs de la respuesta.

## Puertos del host

Los puertos 3000 y 8000 ya estaban ocupados por otros procesos del equipo (PIDs 5108 y 11160 en 3000). No se tocaron. Las pruebas usan 3100 (Compose y E2E), 18000 y 18080.

## Limitaciones pendientes

- CI en GitHub bloqueado por facturación. Pendiente de resolver.
- Verificación real del modelo primario con una llamada a OpenRouter: no ejecutada, requiere autorización y clave propia.
- `buildDeterministicBlock` en `src/lib/metrics-engine.ts` quedó sin uso. Pendiente de limpieza.
- Las pruebas E2E no se ejecutaron contra la imagen Docker. Se ejecutaron contra el build Node en local, y la imagen se verificó por salud y página.
- Python 3.12 se verificó en contenedor oficial, no en la máquina del desarrollador.
