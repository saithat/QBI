FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:${PATH}"

ARG UV_VERSION=0.9.17

WORKDIR /app
COPY pyproject.toml uv.lock README.md .env.example ./
COPY hiveblot ./hiveblot
COPY --from=frontend /hiveblot/static ./hiveblot/static
RUN pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv sync --locked --all-extras --no-editable \
    && useradd --create-home --uid 1000 hiveblot
COPY tests ./tests
RUN chmod -R a+rX /app

USER hiveblot
EXPOSE 8080
CMD ["uvicorn", "hiveblot.api:app", "--host", "0.0.0.0", "--port", "8080"]
