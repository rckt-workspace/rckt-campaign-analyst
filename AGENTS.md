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
- Lovable Cloud is enabled for this project: application tables and functions live in `public`, and new tables need GRANTs plus RLS in the same migration. Why: without grants the Data API rejects every app query.

