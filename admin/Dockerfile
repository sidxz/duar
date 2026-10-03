FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY src/ src/
COPY public/ public/
COPY index.html tsconfig*.json vite.config.ts ./

RUN npm run build

# ──────────────────────────────────────────────────────────────────────

FROM nginx:alpine

RUN apk upgrade --no-cache

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

ENV DUAR_BACKEND=duar:9003
# Path prefix the admin is served under (e.g. /duar-admin). Applied at start, no rebuild.
ENV DUAR_ADMIN_BASE_PATH=/

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost/health || exit 1

LABEL org.opencontainers.image.source="https://github.com/sidxz/identity-service"
LABEL org.opencontainers.image.description="Duar Admin Panel"

# Normalise to "/prefix/" then inject as <base href> so relative asset URLs resolve under it.
CMD p="${DUAR_ADMIN_BASE_PATH%/}/" && p="/${p#/}" \
  && sed -i "s|<base href=\"/\"|<base href=\"$p\"|" /usr/share/nginx/html/index.html \
  && envsubst '$DUAR_BACKEND' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf \
  && nginx -g 'daemon off;'
