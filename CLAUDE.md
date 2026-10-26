# RCKT Campaign Analyst — Claude Code project instructions

## Purpose and working style

This is RCKT's company-owned campaign intelligence product, evolved from an exploratory Lovable MVP. Build a maintainable production-grade application **incrementally**. Don't confuse a Docker configuration with a proven production deployment.

Important: Also read and respect `AGENTS.md` (Lovable sync). Never rewrite published Git history, rebase/amend pushed commits, or force push. Never merge to `main` or deploy externally without the user's explicit request.

Current working branch: `chore/docker-compose-node-production`. PR #1 is a draft and GitHub Actions has failed; investigate before assuming any implementation is functional.

## Existing source of truth

- Frontend: React 19 + TypeScript + TanStack Start/Vite/Nitro. Components in `src/components/campaign/`; main page in `src/routes/index.tsx`.
- Original frontend input normalization: `src/lib/campaign-metrics.ts`.
- Frontend report rendering/markdown contract: `src/lib/report-parser.ts` and `src/components/campaign/ExecutiveReportPanel.tsx`.
- FastAPI: `backend/app/main.py`, `backend/app/api/routes.py`.
- Python domain metrics: `backend/app/domain/metrics.py`.
- Python AI integration: `backend/app/integrations/openrouter.py` and `backend/app/services/analysis.py`.
- Preserved AI instructions: `backend/app/prompts/campaign-analyst.txt`.
- Frontend server-to-server bridge: `src/lib/campaign-api.functions.ts`.
- Two container images: `Dockerfile` (frontend) and `backend/Dockerfile` (FastAPI); `docker-compose.yml` orchestrates.
- Internal API: `POST /api/v1/campaigns/analyze`, response `{ "analysis": "markdown" }`. Health: `GET /healthz`.
- OpenRouter credentials belong exclusively to FastAPI runtime, never VITE_/React/browser. FastAPI must not publish its port publicly.

## Mandatory engineering rules

1. **Inspect, do not assume.** Start each task with `git status --short`, current branch, relevant source files, package/runtime versions and relevant logs. Stop before destructive actions if pre-existing uncommitted work exists.
2. **One phase at a time.** First repair broken baseline: backend tests, TypeScript, frontend build, Docker Compose, smoke test, API integration. Do not start dashboards, RAG, multiagent workflows, authentication, migrations, or external marketing connectors as a side effect of this first phase.
3. **Functional preservation.** Keep current UI, data entry, normalizations, report sections, business safeguards, and OpenRouter primary/fallback behavior. When changing output format, write contract tests first.
4. **Security first.** Never display, log, embed, commit, paste back, or expose real secrets. Use `.env.example` for names only; user owns their local `.env`. Keep external provider error messages private. Add explicit timeouts and validate input sizes. Do not call paid LLM endpoints as part of CI.
5. **Backend authority.** Python/FastAPI performs trusted metrics calculations and AI orchestration; the frontend may preserve instant-preview metrics but should not be the only source of truth for decisions. No duplicated business rules added casually.
6. **Quality gates.** Test real commands and report exact results. Never claim passed without execution. Investigate GitHub Actions separately: a failed job may be code or CI infrastructure, so verify evidence rather than speculate.
7. **Small, auditable changes.** Commit in coherent increments on the feature branch; no force push and no direct push to `main`. Document architectural changes and their rationale.
8. **No overengineering.** Favor clean boundaries, explicit dependencies, typed contracts, tests and observability hooks. PostgreSQL, tenant isolation and user authentication are planned product work, not required for the first stabilization slice.
9. **Safe deployment.** Default binds the frontend to localhost. Do not expose an unauthenticated model-backed product to the public. Public launch requires TLS, authentication, per-user quotas/rate limits, provider budgets, monitoring and rollback.
10. **Communication.** Respond to the user in clear Spanish, with a concise result: what changed, what commands passed/failed, paths modified, risks, commit, and immediate next step.

## Phase 1 — Acceptance criteria (do these first)

- Backend dependencies install without errors in Python 3.12.
- `cd backend && python -m pytest -q tests` passes with mock OpenRouter.
- `bun install --frozen-lockfile`, `bunx tsc --noEmit`, and Node-targeted production build pass.
- `docker compose config --quiet`, `docker compose build`, `docker compose up -d` pass.
- Backend `/healthz` responds inside its container and frontend page responds on `127.0.0.1:3000`.
- Mocked/fixture end-to-end request confirms React/TanStack -> internal FastAPI -> expected report contract, without billing OpenRouter.
- A real end-to-end OpenRouter call only if a local key is already securely configured and the user approves consuming provider quota.
- CI status verified; no secrets leaked; no frontend redesign or unrequested database additions.
- Commit fixes on the feature branch, keeping PR #1 in draft until all applicable gates pass.

Read `docs/PRODUCT_ROADMAP.md` for future work, but implement only the current phase.

## Closed data access (decision, Phase 2.5A)

- Tables that only FastAPI reads and writes (`analyses`, `analysis_usage`, and the campaign tables) keep row level
  security enabled with **no policies** and **no grants** to `anon` or `authenticated`. Only the owner role used by
  FastAPI reaches them. Why: the Data API must not expose analysis data until identity integration is verified.
- Do not add permissive policies, general grants or public views without an explicit decision recorded in `docs/`.
- `AGENTS.md` carries the same decision (its GRANT rule was narrowed on 2026-10-26). Both files now agree.
- A migration applied anywhere, including the local development database, is never edited. Corrections go in a new migration.
