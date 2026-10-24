# syntax=docker/dockerfile:1
# TanStack Start: frontend, server functions and SSR in one Nitro server.
FROM oven/bun:1.3-alpine AS builder
WORKDIR /app

# Keep Bun in sync with the committed lockfile.
COPY package.json bun.lock bunfig.toml ./
RUN bun install --frozen-lockfile

COPY . .
# Outside Lovable, build a standalone Node.js server instead of Cloudflare output.
ENV RCKT_DEPLOY_TARGET=node
RUN bun run build && test -f .output/server/index.mjs

FROM node:22-alpine AS runner
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000

WORKDIR /app
COPY --from=builder --chown=node:node /app/.output ./.output
USER node

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", ".output/server/index.mjs"]
