FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_DIR=/app/artifacts/model
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-ml.txt ./
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-ml.txt \
    && pip check
COPY ml/ ml/
COPY backend/ backend/
COPY ndtp_ingestion/ ndtp_ingestion/
COPY scripts/ scripts/
COPY frontend/dispatcher/ frontend/dispatcher/
COPY artifacts/model/ artifacts/model/
RUN useradd --uid 10001 --create-home app \
    && mkdir -p /data/input /data/results /data/runtime \
    && chown -R app:app /data
USER app
EXPOSE 8000 9000
CMD ["python", "-m", "uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
