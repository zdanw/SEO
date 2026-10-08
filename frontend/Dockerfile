# SEO 平台前端镜像：Node 构建 → Nginx 托管静态产物并反代 /api
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# build = vue-tsc 类型检查 + vite build
RUN npm run build

FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
