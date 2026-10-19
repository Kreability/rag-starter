# Node 22 LTS: current pnpm requires >= 22.13, and Next.js 16 needs >= 20.9.
FROM node:22-slim AS base

ENV NEXT_TELEMETRY_DISABLED=1 \
    PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH

# The pnpm version is pinned here, not in package.json's "packageManager".
# Do NOT use `pnpm@latest`: that tracks whatever pnpm published most recently,
# so an unrelated upstream major bump changes the toolchain under a
# lockfile-pinned workspace. A "packageManager" field would also make pnpm
# self-manage — it installs that pnpm as a dependency and rewrites
# pnpm-lock.yaml with ~160 lines of @pnpm/exe entries for every platform, and
# the `web` container bind-mounts this workspace and installs on every boot,
# so that dirtied a tracked file as a side effect of `docker compose up`.
ARG PNPM_VERSION=12.6.0
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare "pnpm@${PNPM_VERSION}" --activate

WORKDIR /app

EXPOSE 3000

# --- development: workspace + toolchain, sources bind-mounted at run time ----
# The compose `web` service targets this stage and overrides the command with
# `pnpm install -r && pnpm --filter web dev`.
#
# It must run as root. Docker creates the anonymous node_modules volumes owned
# by root, and this stage's install has to write into them; inheriting the
# production stage's `USER node` (uid 1000) instead made every symlink fail with
# "Permission denied (os error 13)" and the container restart-looped.
FROM base AS development

# Nothing is pre-installed here on purpose: `web` bind-mounts ./frontend over
# /app and masks every node_modules with anonymous volumes, so anything
# installed at build time would be discarded at run time.

# --- builder: installs the whole workspace and produces a standalone build ----
FROM base AS builder

# Copy the full workspace. The lockfile alone is not enough: `pnpm install`
# needs every package.json referenced by pnpm-workspace.yaml to resolve links.
COPY . .

RUN pnpm install -r --frozen-lockfile

RUN pnpm --filter web build

# `output: 'standalone'` deliberately omits public/ and .next/static — they are
# expected to come from a CDN. We serve them from the node process instead, so
# copy them into the standalone tree where server.js already looks for them.
# In a pnpm workspace the standalone tree mirrors the repo layout, hence the
# apps/web prefix.
RUN cp -r apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static && \
    if [ -d apps/web/public ]; then \
        cp -r apps/web/public apps/web/.next/standalone/apps/web/public; \
    fi

# --- production: just the standalone bundle, no pnpm store, no sources -------
FROM base AS production

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# node:*-slim already ships an unprivileged `node` user (uid 1000).
COPY --from=builder --chown=node:node /app/apps/web/.next/standalone ./

USER node

# The standalone tree keeps the workspace layout, so server.js sits under
# apps/web rather than at the root.
CMD ["node", "apps/web/server.js"]
