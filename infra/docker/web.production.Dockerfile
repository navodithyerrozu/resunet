# syntax=docker/dockerfile:1
FROM node:20-bookworm-slim AS dependencies
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
WORKDIR /srv
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/
COPY packages/shared/package.json ./packages/shared/
RUN pnpm install --frozen-lockfile

FROM dependencies AS builder
COPY packages/shared/ ./packages/shared/
COPY apps/web/ ./apps/web/
RUN pnpm --filter @resunet/shared build \
    && pnpm --filter @resunet/web build

FROM node:20-bookworm-slim AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /srv
RUN groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid nodejs nextjs
COPY --from=builder --chown=nextjs:nodejs /srv/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /srv/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /srv/apps/web/public ./apps/web/public
USER nextjs
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
