ARG NODE_IMAGE=public.ecr.aws/docker/library/node:22-alpine
ARG NGINX_IMAGE=public.ecr.aws/docker/library/nginx:alpine

FROM ${NODE_IMAGE} AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

ARG VITE_COGNITRADE_API_BASE_URL=/api/v1
ENV VITE_COGNITRADE_API_BASE_URL=${VITE_COGNITRADE_API_BASE_URL}

RUN npm run lint \
    && npm run build

FROM ${NGINX_IMAGE}

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
