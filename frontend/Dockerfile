# Multi-stage: build the static bundle with Node, serve it with nginx -- the runtime image
# never contains node_modules, source, or the toolchain, only the ~230-780kB of built assets
# (see PLAN.md Phase 10 for the route-based code splitting that got it down from the larger
# number). Vite requires Node ^20.19 || >=22.12; 22-alpine satisfies that.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
