# Stage 1: Install dependencies
FROM node:22-alpine AS deps

ARG HTTP_PROXY
ARG HTTPS_PROXY

WORKDIR /app/davis_webui/frontend

COPY davis_webui/frontend/package.json davis_webui/frontend/package-lock.json ./

RUN npm ci

# Stage 2: Build
FROM node:22-alpine AS builder

WORKDIR /app

COPY davis_webui/ ./davis_webui/
COPY davis_analyzer/ ./davis_analyzer/
COPY --from=deps /app/davis_webui/frontend/node_modules ./davis_webui/frontend/node_modules

WORKDIR /app/davis_webui/frontend

RUN npm run build

# Stage 3: Production runner
FROM alpine:3.21 AS runner

COPY --from=node:22-alpine /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-alpine /usr/local/include/node /usr/local/include/node
COPY --from=node:22-alpine /usr/local/lib/node_modules /usr/local/lib/node_modules

WORKDIR /app

RUN apk add --no-cache libstdc++ && \
    addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/davis_webui/frontend/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/davis_webui/frontend/.next/static ./davis_webui/frontend/.next/static

USER nextjs

ENV NODE_ENV=production
ENV HOSTNAME=127.0.0.1
ENV PORT=3100

EXPOSE 3100

CMD ["node", "davis_webui/frontend/server.js"]
