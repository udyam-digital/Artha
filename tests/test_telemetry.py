from __future__ import annotations

from pathlib import Path
import builtins

from config import Settings
import observability.telemetry as telemetry
from observability.telemetry import build_exporter_config, emit_span, initialize_telemetry, shutdown_telemetry, start_span


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base = {
        "ANTHROPIC_API_KEY": "test-key",
        "REPORTS_DIR": str(tmp_path / "reports"),
        "LLM_USAGE_DIR": str(tmp_path / "reports" / "usage"),
        "KITE_DATA_DIR": str(tmp_path / "kite"),
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
    }
    base.update(overrides)
    return Settings(**base)


def test_build_exporter_config_prefers_langfuse(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        LANGFUSE_PUBLIC_KEY="pk-lf-test",
        LANGFUSE_SECRET_KEY="sk-lf-test",
        LANGFUSE_BASE_URL="https://us.cloud.langfuse.com",
    )
    endpoint, headers, backend = build_exporter_config(settings)  # type: ignore[misc]
    assert backend == "langfuse"
    assert endpoint == "https://us.cloud.langfuse.com/api/public/otel/v1/traces"
    assert headers["Authorization"].startswith("Basic ")


def test_build_exporter_config_uses_generic_otlp_when_langfuse_missing(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        OTEL_EXPORTER_OTLP_ENDPOINT="https://otel.example.com/custom",
        OTEL_EXPORTER_OTLP_HEADERS='{"x-test":"1"}',
    )
    endpoint, headers, backend = build_exporter_config(settings)  # type: ignore[misc]
    assert backend == "otlp"
    assert endpoint == "https://otel.example.com/custom"
    assert headers == {"x-test": "1"}


def test_redact_endpoint_for_logs_removes_query_and_credentials() -> None:
    redacted = telemetry._redact_endpoint_for_logs(  # noqa: SLF001
        "https://user:secret@example.com:4318/v1/traces?api_key=secret-token"
    )
    assert redacted == "https://example.com:4318/v1/traces"


def test_build_exporter_config_returns_none_when_disabled_or_unconfigured(tmp_path: Path) -> None:
    disabled = make_settings(tmp_path, TELEMETRY_ENABLED=False)
    assert build_exporter_config(disabled) is None
    unconfigured = make_settings(tmp_path)
    assert build_exporter_config(unconfigured) is None

    shutdown_telemetry()
    assert initialize_telemetry(disabled) is False
    with start_span("disabled-span") as span:
        assert span is None
    emit_span("disabled-event", {"foo": "bar"})


def test_initialize_and_shutdown_telemetry_with_otlp(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318/v1/traces",
        TELEMETRY_SERVICE_NAME="artha-test",
        TELEMETRY_ENVIRONMENT="test",
    )
    shutdown_telemetry()
    assert initialize_telemetry(settings) is True
    with start_span("artha.test", {"foo": "bar"}) as span:
        assert span is not None
        span.set_attribute("another", "value")
    with start_span("artha.none", {"skip": None}) as span:
        assert span is not None
    emit_span("artha.event", {"x": 1})
    assert initialize_telemetry(settings) is True
    shutdown_telemetry()


def test_initialize_telemetry_handles_missing_opentelemetry_dependency(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(
        tmp_path,
        OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318/v1/traces",
    )
    shutdown_telemetry()

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name.startswith("opentelemetry.exporter") or name.startswith("opentelemetry.sdk"):
            raise ImportError("missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert telemetry.initialize_telemetry(settings) is False
    shutdown_telemetry()
