FROM node:22-alpine AS build
WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build
FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
EXPOSE 3000
CMD ["node","server.js"]
