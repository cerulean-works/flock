# syntax=docker/dockerfile:1
# The dashboard as a Node server (adapter-node). ORIGIN, the address people open, is fixed at
# build time; FLOCK_URL and FLOCK_PUBLIC_URL are read at run time (see README). compose.yaml
# runs it with Flock.
#   docker build --build-arg ORIGIN=https://flock.example.com -t flock-ui .

FROM node:24-slim AS build
RUN npm install --global pnpm@12.4.1
WORKDIR /app

# Dependencies first, so source changes don't invalidate this layer.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .
ARG ORIGIN=http://localhost:3000
RUN ORIGIN=$ORIGIN pnpm build


# Every package is bundled into build/ at build time: the server needs no node_modules.
FROM node:24-slim
WORKDIR /app
COPY --from=build /app/package.json ./
COPY --from=build /app/build ./build
# Requests up to 4 MB, over adapter-node's 512K default: room for uploads such as avatars
# (Flock still caps those at 1 MiB).
ENV NODE_ENV=production \
    PORT=3000 \
    BODY_SIZE_LIMIT=4M
USER node
EXPOSE 3000
# A static file: healthy means the server answers, whether or not Flock does.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/robots.txt').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["node", "build"]
