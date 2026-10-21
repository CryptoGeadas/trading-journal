FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# App code
COPY app/ app/
COPY frontend/ frontend/

# User data lives in a volume outside the app folder
ENV TRADING_JOURNAL_DATA_DIR=/data
RUN mkdir -p /data
VOLUME /data

EXPOSE 8000

# 0.0.0.0 is needed INSIDE the container so Docker can forward the port.
# Who can reach it is decided when publishing the port: docker-compose.yml
# publishes it on 127.0.0.1 only (this computer). With `docker run`, use
# `-p 127.0.0.1:8000:8000`, not `-p 8000:8000`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
