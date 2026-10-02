# syntax=docker/dockerfile:1
# Aegis frontend: build the SPA with Node, serve it with unprivileged nginx.
#   docker build -t aegis-frontend:dev frontend

FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
