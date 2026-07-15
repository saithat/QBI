FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY hiveblot ./hiveblot
RUN pip install --no-cache-dir ".[dev]" \
    && useradd --create-home --uid 1000 hiveblot
COPY tests ./tests

USER hiveblot
EXPOSE 8080
CMD ["uvicorn", "hiveblot.api:app", "--host", "0.0.0.0", "--port", "8080"]
