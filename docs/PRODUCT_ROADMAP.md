# RCKT Campaign Analyst — Product and architecture roadmap

**Status:** proposal for iterative delivery, not a list of already-built features. Prioritize measurable business outcomes and production reliability.

## Business outcome

Convert digital campaign metrics into understandable, traceable recommendations that help an analyst, manager, or client evaluate campaign performance and decide how to improve revenue efficiency. Never claim real-time connections or financial causality without supporting evidence.

## Verified baseline

Lovable-origin React interface, campaign text/quick-form input, metric normalization and deterministic checks, executive report renderer, OpenRouter single-agent prompt and primary/fallback provider. The initial migration to FastAPI + Docker Compose lives on a feature branch and is **not yet verified in production**.

## Phase 1: Stabilize application and integration

- Repair environment/dependency failures, backend tests, TypeScript and Docker builds.
- Confirm contract of `/api/v1/campaigns/analyze`, input validation, error handling, timeouts, safe logs and system health.
- Test metrics: CTR, CPC, CPL, CPA, ROAS, frequency, conversion rate, period comparisons and contradictions.
- Full browser-to-server-to-FastAPI integration using mocked provider (no secret or external charge).
- Preserve Lovable-origin UI and report structure.
- Add CI reliability and runbook. Do not publish public endpoint.

**Exit:** two-container Compose can be launched from a clean checkout and the full interaction is validated reproducibly.

## Phase 2: Data and enterprise access (separate scope)

- PostgreSQL with migrations, ownership and retention policies.
- Tenant/organization boundaries and a clear authorization model.
- Login and roles (owner, analyst, read-only client), secure sessions.
- Campaign entities, analysis history, evidence for AI results, auditability.
- Rate limits/usage quotas, operational budget and access controls.

**Exit:** users can securely store and access only the campaigns authorized for their organization.

## Phase 3: Analytics product

- Campaign comparisons over time and across channels.
- Explain costs, attributed revenue, ROAS and conversion efficiency with data-quality warnings.
- Report dashboards that distinguish source data, calculated metrics and AI opinions.
- Hypothesis-driven recommendations with evidence and measurable follow-up.
- Avoid fictitious benchmarks or unsupported "revenue improvements".

## Phase 4: AI quality and specialist workflows

- Strongly typed/structured report outputs with schema validation and error recovery.
- Evaluation dataset covering incomplete, inconsistent, contradictory and adversarial inputs.
- Versioned prompts and provenance of model/provider; trace tokens/cost safely.
- Agent tools or specialist agents **only** if measured performance justifies them.
- Optional document retrieval later if an actual business knowledge base exists.

## Phase 5: Integrations and customer delivery

- Explicitly authorized provider integrations (e.g., Meta Ads, Google Ads, GA4, CRM).
- OAuth and token management, sync jobs, idempotency, retries, provider quotas and backfill.
- Scheduled customer reports, export and notification capabilities.
- HTTPS, observability/SLOs, backups, rollbacks and deployment playbook.

## Governance and decision rules

- Every new feature must have a user scenario, acceptance criteria and tests.
- Prefer one shippable end-to-end vertical slice over many unfinished modules.
- Architectural flexibility is a goal, not an excuse for unnecessary microservices.
- FastAPI and the frontend deploy as separate containers via one Compose stack initially; split further only when bottlenecks justify it.
- Changes remain on the feature branch until reviewed, tested and explicitly approved for merge.
