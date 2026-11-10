FROM node:26-alpine AS build

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
# "true" when a reverse proxy in front of this container already appends the
# client address to X-Forwarded-For: the header is then passed on as received,
# so the service sees the same proxy chain for panel requests as for direct
# ones. "false" (the panel is the first hop): this nginx appends the client itself.
ENV DUAR_ADMIN_BEHIND_PROXY=false

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/health || exit 1

LABEL org.opencontainers.image.source="https://github.com/sidxz/identity-service"
LABEL org.opencontainers.image.description="Duar Admin Panel"

# Normalise to "/prefix/" then inject as <base href> so relative asset URLs resolve under it.
CMD p="${DUAR_ADMIN_BASE_PATH%/}/" && p="/${p#/}" \
  && sed -i "s|<base href=\"/\"|<base href=\"$p\"|" /usr/share/nginx/html/index.html \
  && if [ "$DUAR_ADMIN_BEHIND_PROXY" = "true" ]; then f='$http_x_forwarded_for'; else f='$proxy_add_x_forwarded_for'; fi \
  && DUAR_ADMIN_FORWARDED_FOR="$f" envsubst '$DUAR_BACKEND $DUAR_ADMIN_FORWARDED_FOR' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf \
  && nginx -g 'daemon off;'
