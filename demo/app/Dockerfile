# Demo services image (frontend/checkout/catalog/payments roles, flag service, load generator).
FROM python:3.13-slim
RUN useradd --create-home --uid 10001 demo
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY service.py flags.py loadgen.py ./
USER demo
ENV PYTHONUNBUFFERED=1 \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
    OTEL_METRIC_EXPORT_INTERVAL=5000
EXPOSE 8080
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
