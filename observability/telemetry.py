from __future__ import annotations

import base64
import logging
from contextlib import nullcontext
from typing import Any

from config import Settings


logger = logging.getLogger(__name__)

_TELEMETRY_INITIALIZED = False
_TELEMETRY_ENABLED = False
_TRACER: Any | None = None
_PROVIDER: Any | None = None


def _langfuse_auth_header(settings: Settings) -> str:
    token = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode("utf-8")
    encoded = base64.b64encode(token).decode("ascii")
    return f"Basic {encoded}"


def build_exporter_config(settings: Settings) -> tuple[str, dict[str, str], str] | None:
    if not settings.telemetry_enabled:
        return None
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        base_url = settings.langfuse_base_url.rstrip("/")
        return (
            f"{base_url}/api/public/otel/v1/traces",
            {"Authorization": _langfuse_auth_header(settings)},
            "langfuse",
        )
    endpoint = settings.otel_exporter_otlp_endpoint.strip()
    if not endpoint:
        return None
    return (endpoint.rstrip("/"), dict(settings.otel_exporter_otlp_headers), "otlp")


def initialize_telemetry(settings: Settings) -> bool:
    global _TELEMETRY_INITIALIZED, _TELEMETRY_ENABLED, _TRACER, _PROVIDER
    if _TELEMETRY_INITIALIZED:
        return _TELEMETRY_ENABLED

    exporter_config = build_exporter_config(settings)
    if exporter_config is None:
        _TELEMETRY_INITIALIZED = True
        _TELEMETRY_ENABLED = False
        return False

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "Telemetry export was configured but OpenTelemetry packages are not installed. "
            "Install requirements.txt to enable Langfuse/OTLP tracing."
        )
        _TELEMETRY_INITIALIZED = True
        _TELEMETRY_ENABLED = False
        return False

    endpoint, headers, backend = exporter_config
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.telemetry_service_name,
                "deployment.environment": settings.telemetry_environment,
            }
        )
    )
    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    _PROVIDER = provider
    _TRACER = provider.get_tracer(settings.telemetry_service_name)
    _TELEMETRY_INITIALIZED = True
    _TELEMETRY_ENABLED = True
    logger.info("Telemetry initialized via %s exporter to %s", backend, endpoint)
    return True


def start_span(name: str, attributes: dict[str, Any] | None = None) -> Any:
    if not _TELEMETRY_ENABLED or _TRACER is None:
        return nullcontext(None)
    span_cm = _TRACER.start_as_current_span(name)
    span = span_cm.__enter__()
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        span.set_attribute(key, value)
    return _ManagedSpan(span_cm=span_cm, span=span)


class _ManagedSpan:
    def __init__(self, *, span_cm: Any, span: Any):
        self._span_cm = span_cm
        self.span = span

    def __enter__(self) -> Any:
        return self.span

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._span_cm.__exit__(exc_type, exc, tb)


def emit_span(name: str, attributes: dict[str, Any] | None = None) -> None:
    with start_span(name, attributes):
        return


def shutdown_telemetry() -> None:
    global _TELEMETRY_INITIALIZED, _TELEMETRY_ENABLED, _TRACER, _PROVIDER
    if _PROVIDER is not None:
        _PROVIDER.shutdown()
    _TELEMETRY_INITIALIZED = False
    _TELEMETRY_ENABLED = False
    _TRACER = None
    _PROVIDER = None
