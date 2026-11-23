# Stage 1: build the web app
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: API + built UI
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY services ./services
COPY sdk ./sdk
COPY agents ./agents
COPY eval ./eval
COPY --from=web /web/dist ./web/dist
ENV PYTHONPATH=/app/services/api:/app/sdk/python:/app/agents \
    VARUNA_DATA_DIR=/data \
    VARUNA_WEB_DIST=/app/web/dist
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/healthz')"
CMD ["uvicorn", "varuna.main:app", "--host", "0.0.0.0", "--port", "8000"]
