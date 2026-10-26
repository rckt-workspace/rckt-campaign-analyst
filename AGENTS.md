<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Rules

- Keep every AI provider credential and call server-side (`*.server.ts` only); the browser must never receive a key. Why: the frontend is public and a published key can be billed or abused.
- Before changing `CHAT_PRIMARY_LLM` / `CHAT_FALLBACK_LLM`, verify the candidate with a real analysis request and confirm the reply contains every required section — OpenRouter free-tier slugs are pulled or rate-limited often, and the app's output budget (`CHAT_MAX_TOKENS`) must fit the whole report. Why: a model that "responds" but truncates or returns empty content breaks the report silently.
- Lovable Cloud is enabled for this project: application tables and functions live in `public`, and new tables need RLS enabled in the same migration. Tables that the Data API must read get explicit GRANTs and policies, reviewed case by case. Tables that only FastAPI reads and writes (`analyses`, `analysis_usage`, `campaigns`, `campaign_metric_entries`) get no GRANT to `anon` or `authenticated` and no policies: their access stays closed until identity is verified. Why: FastAPI is the only trusted path to them. Decision and limits: docs/PHASE2_ANALYSES.md, section 6, and CLAUDE.md, 'Closed data access'.

