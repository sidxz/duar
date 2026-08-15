FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY src/ src/
COPY public/ public/
COPY index.html tsconfig*.json vite.config.ts ./

ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}

RUN npm run build

# ──────────────────────────────────────────────────────────────────────

FROM nginx:alpine

RUN apk upgrade --no-cache

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

ENV DUAR_BACKEND=duar:9003

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost/health || exit 1

LABEL org.opencontainers.image.source="https://github.com/sidxz/identity-service"
LABEL org.opencontainers.image.description="Duar Admin Panel"

CMD ["/bin/sh", "-c", "envsubst '$DUAR_BACKEND' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
