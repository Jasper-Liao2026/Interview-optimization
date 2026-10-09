# syntax=docker/dockerfile:1

FROM node:22-alpine AS base
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH
RUN corepack enable && corepack prepare pnpm@11.5.0 --activate
WORKDIR /repo

# ---------- 依赖层 ----------
FROM base AS deps
# 锁文件已入库，构建按 --frozen-lockfile 走，确保镜像里的依赖版本与 CI / 本地一致
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY apps/web/package.json apps/web/
COPY packages/api-types/package.json packages/api-types/
RUN --mount=type=cache,target=/pnpm/store pnpm install --frozen-lockfile

# ---------- 构建层 ----------
FROM deps AS builder
COPY . .
# standalone 产物：只带运行时真正需要的文件，镜像小一半
RUN pnpm --filter @resume/web build

# ---------- 运行层 ----------
FROM node:22-alpine AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/public ./apps/web/public

USER nextjs
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
