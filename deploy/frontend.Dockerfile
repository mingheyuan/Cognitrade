ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:alpine

FROM ${NODE_IMAGE} AS build

ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry="${NPM_CONFIG_REGISTRY}"

COPY frontend/ ./

ARG VITE_COGNITRADE_API_BASE_URL=/api/v1
ENV VITE_COGNITRADE_API_BASE_URL=${VITE_COGNITRADE_API_BASE_URL}

RUN npm run lint \
    && npm run build

FROM ${NGINX_IMAGE}

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
