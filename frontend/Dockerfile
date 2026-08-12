# Node 22 LTS: current pnpm requires >= 22.13, and Next.js 16 needs >= 20.9.
FROM node:22-slim AS base

ENV NEXT_TELEMETRY_DISABLED=1 \
    PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH

# `corepack` ships the pnpm version pinned by the repo rather than a global one.
RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app

EXPOSE 3000
