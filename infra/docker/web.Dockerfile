# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Next.js frontend. Dev-oriented image (Compose mounts source over it and
# runs `pnpm dev`). For production, add a `next build` stage and run the
# standalone output.
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    NEXT_TELEMETRY_DISABLED=1

RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

WORKDIR /srv

# Workspace manifests first so the install layer caches independently of source.
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/web/package.json ./apps/web/
COPY packages/shared/package.json ./packages/shared/
RUN pnpm install --frozen-lockfile || pnpm install

COPY packages/shared/ ./packages/shared/
COPY apps/web/ ./apps/web/

WORKDIR /srv/apps/web

EXPOSE 3000

CMD ["pnpm", "dev"]
