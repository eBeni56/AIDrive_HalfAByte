# Build the React app, then serve it with nginx (which also passes /ws to the server container).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# The WebSocket goes to this page's own host (/ws, see nginx.conf); the camera stream is read by the
# browser directly from the driver monitor (on this PC, or the replay container's published port).
ARG VITE_WS_URL=/ws
ARG VITE_CAMERA_URL=http://localhost:8090/stream.mjpg
ENV VITE_WS_URL=$VITE_WS_URL VITE_CAMERA_URL=$VITE_CAMERA_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
