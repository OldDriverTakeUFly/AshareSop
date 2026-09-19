# Stage 1: Install dependencies
FROM node:22-alpine AS deps

WORKDIR /app/dashboard

COPY dashboard/package.json dashboard/package-lock.json ./

RUN npm ci

# Stage 2: Build
FROM node:22-alpine AS builder

WORKDIR /app

# outputFileTracingRoot resolves to /app here — mirrors the project layout
COPY dashboard/ ./dashboard/
COPY --from=deps /app/dashboard/node_modules ./dashboard/node_modules

WORKDIR /app/dashboard

RUN npm run build

# Stage 3: Production runner (copy only node binary from full image, no npm/yarn)
FROM alpine:3.21 AS runner

COPY --from=node:22-alpine /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-alpine /usr/local/include/node /usr/local/include/node
COPY --from=node:22-alpine /usr/local/lib/node_modules /usr/local/lib/node_modules

WORKDIR /app

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/dashboard/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/dashboard/.next/static ./dashboard/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/dashboard/public ./dashboard/public

USER nextjs

ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

EXPOSE 3000

CMD ["node", "dashboard/server.js"]
