# 1) React-Webpanel bauen
FROM node:20-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund && test -x node_modules/.bin/tsc && test -x node_modules/.bin/vite
COPY . .
RUN npm run build

# 2) Nginx als Reverse Proxy (HTTPS innerhalb des Tailscale-VPN)
FROM nginx:1.27-alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/leitstelle.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80 443
