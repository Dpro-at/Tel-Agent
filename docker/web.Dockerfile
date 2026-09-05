# The dashboard - §B10.
#
# Build context is the repository root, because the UI dictionaries live in
# `locales/`, one level above `web/`, and the build imports them:
#   docker build -f docker/web.Dockerfile .
#
# NEXT_PUBLIC_API_URL is baked at build time - it is a browser-side constant, and
# the CSP's connect-src is derived from it. The default serves an installation
# reached as http://localhost:38471 with the API published on localhost:38472. An
# installation reached under another name rebuilds with its own value; the compose
# file wires that through TEL_AGENT_PUBLIC_API_URL.

FROM node:22-alpine AS build

WORKDIR /repo

COPY web/package.json web/package-lock.json web/
RUN cd web && npm ci

COPY locales/ locales/
COPY web/ web/
# web/public does not exist in the repository today (assets ride the CDN), and a
# COPY of a missing directory fails the build. Guaranteed here so the runtime
# stage can copy it unconditionally; Next serves an empty one happily.
RUN mkdir -p web/public

ARG NEXT_PUBLIC_API_URL=http://localhost:38472
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NODE_ENV=production
RUN cd web && npm run build

# ---------------------------------------------------------------------------

FROM node:22-alpine

WORKDIR /app

# `outputFileTracingRoot` is the repository, so the standalone bundle keeps the
# repo shape: the server entry is web/server.js under it. `node` is the non-root
# user the base image already ships.
COPY --from=build --chown=node:node /repo/web/.next/standalone ./
COPY --from=build --chown=node:node /repo/web/.next/static ./web/.next/static
COPY --from=build --chown=node:node /repo/web/public ./web/public

USER node

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=38471

EXPOSE 38471

CMD ["node", "web/server.js"]
