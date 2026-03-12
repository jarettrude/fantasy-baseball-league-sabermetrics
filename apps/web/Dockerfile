FROM node:22-alpine AS builder

WORKDIR /app

# Copy package files for dependency installation
COPY apps/web/package.json apps/web/pnpm-lock.yaml* ./
RUN npm install -g pnpm && pnpm install --frozen-lockfile

# Copy source (package files already copied above)
COPY apps/web/src ./src
COPY apps/web/public ./public
COPY apps/web/astro.config.mjs apps/web/tsconfig.json ./
COPY apps/web/svelte.config.js apps/web/eslint.config.js apps/web/prettier.config.mjs ./
COPY apps/web/pnpm-workspace.yaml ./

# Build-time variables for Astro baking
ARG PUBLIC_API_URL
ENV PUBLIC_API_URL=$PUBLIC_API_URL

# Build Astro
RUN npm run build

# --- Runner Stage ---
FROM node:22-alpine AS runner
WORKDIR /app

# Install pnpm
RUN npm install -g pnpm

# Copy built app
COPY --from=builder /app/dist ./dist
# IMPORTANT: We need package.json to run in production mode
COPY --from=builder /app/package.json ./
COPY --from=builder /app/pnpm-lock.yaml ./

# Install ONLY production dependencies
RUN pnpm install --prod --frozen-lockfile

ENV HOST=0.0.0.0
ENV PORT=4321
EXPOSE 4321

# Use the absolute path to the entry file
CMD ["node", "./dist/server/entry.mjs"]
