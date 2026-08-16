FROM node:24-alpine AS build

WORKDIR /app

COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY apps/web/index.html apps/web/redirect.html apps/web/vite.config.js ./
COPY apps/web/src ./src

RUN --mount=type=secret,id=web_env,target=/app/.env,required=true \
    npm run build

FROM nginx:1.29-alpine

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

