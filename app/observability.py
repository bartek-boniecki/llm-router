"""
Prometheus metrics + OpenTelemetry tracing (exporter is optional).
Docs: Prometheus client (0.22.x), OTEL Python SDK (1.27.0).
"""

from fastapi import FastAPI
from starlette.responses import Response
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider

from app.config import settings

# We create a tiny FastAPI app ONLY for /metrics, then mount it under the main app at /metrics.
metrics_app = FastAPI()

@metrics_app.get("/")  # <-- important: must be "/" so mounting at "/metrics" works
def metrics_root():
    # generate_latest() returns the plaintext exposition format Prometheus expects
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

# Global Prometheus metrics (will be visible at /metrics)
REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", labelnames=("endpoint", "method")
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Request latency", labelnames=("endpoint", "method")
)

def configure_tracer() -> None:
    """
    Optional: if an OTLP exporter endpoint is set, wire up OpenTelemetry.
    This lets you ship traces to a backend like Tempo/Jaeger/CloudTrace.
    """
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        resource = Resource.create({"service.name": settings.SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        processor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
        )
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

# Run tracer setup at import time so the main app gets instrumented
configure_tracer()
