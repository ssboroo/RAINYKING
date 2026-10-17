FROM node:22-bookworm-slim AS web-build
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_PUBLIC_API_URL="" NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends nginx gettext-base libstdc++6 bash poppler-utils tesseract-ocr-mon tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY --from=web-build /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps --only-shell chromium
COPY backend/app ./app
COPY --from=web-build /web/.next/standalone ./web
COPY --from=web-build /web/.next/static ./web/.next/static
COPY deploy/ ./deploy/
RUN mkdir -p /data && chmod +x /app/deploy/start.sh
ENV ENABLE_BROWSER_EXTRACTION=true ENABLE_PDF_OCR=true PORT=8080 NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PYTHONUNBUFFERED=1 DATABASE_URL=sqlite:////data/transparency.db
EXPOSE 8080
CMD ["/bin/bash", "/app/deploy/start.sh"]
