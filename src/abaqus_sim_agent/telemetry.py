"""显式启用的本机 Phoenix 接线；运行状态与验收仍由宿主拥有。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from importlib.metadata import version
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import requests
from opentelemetry import baggage, context, trace
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import (
    Event,
    ReadableSpan,
    Span,
    SpanProcessor,
    TracerProvider,
)
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.util.types import AttributeValue
from pydantic_ai import InstrumentationSettings

if TYPE_CHECKING:
    from fastapi import FastAPI

_logger = logging.getLogger(__name__)
_provider: TracerProvider | None = None
_settings: InstrumentationSettings | None = None
_environment: str | None = None
_endpoint: str | None = None
_credential_values: tuple[str, ...] = ()
SCHEMA_VERSION = "abaqus-phoenix-v1"
SHUTDOWN_TIMEOUT_MILLIS = 2000
DEFAULT_ENDPOINT = "http://127.0.0.1:6006"
PROJECT_NAME = "abaqus-sim-agent"
TELEMETRY_CREDENTIAL_ENV_PATTERNS = (
    "PHOENIX_API_KEY",
    "PHOENIX_CLIENT_HEADERS",
    "OTEL_EXPORTER_OTLP*HEADERS",
)
_SDK_ENV_KEYS = (
    "PHOENIX_COLLECTOR_ENDPOINT",
    "PHOENIX_GRPC_PORT",
    "PHOENIX_DISCOVER_CONFIG",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)
_ID_KEYS = ("run_id", "invocation_id", "execution_id", "case_id", "batch_id")


def add_phoenix_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--phoenix",
        action="store_true",
        help="Send traces and message/tool/case contents to local Phoenix (default 127.0.0.1:6006; may contain private text)",
    )


def local_endpoint(value: str) -> str:
    """校验本机无鉴权 HTTP 服务地址，返回不含 OTLP 路径的根 URL。"""
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("PHOENIX_COLLECTOR_ENDPOINT has an invalid port") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") not in {"", "/v1/traces"}
        or port == 0
    ):
        raise ValueError(
            "Phoenix requires a loopback HTTP endpoint without credentials, query or fragment"
        )
    return f"http://{parsed.netloc}"


@contextmanager
def _sdk_environment(endpoint: str) -> Iterator[None]:
    keys = (
        *_SDK_ENV_KEYS,
        *(
            key
            for key in os.environ
            if key.startswith("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_")
        ),
    )
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = endpoint
        os.environ["PHOENIX_DISCOVER_CONFIG"] = "false"
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _IdentityProcessor(SpanProcessor):
    def on_start(
        self, span: Span, parent_context: context.Context | None = None
    ) -> None:
        attributes = {
            key: value
            for key in _ID_KEYS
            if isinstance(
                value := baggage.get_baggage(key, context=parent_context), str
            )
        }
        if run_id := attributes.get("run_id"):
            attributes["session.id"] = run_id
        span.set_attributes(attributes)


class _CredentialExporter(SpanExporter):
    """在发送边界过滤宿主已知密钥，保留本地 history、原异常和 span 身份。"""

    def __init__(self, delegate: SpanExporter, secrets: tuple[str, ...]):
        self.delegate = delegate
        self.secrets = secrets

    def _text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[Scrubbed]")
        return value

    def _attributes(
        self, attributes: Mapping[str, AttributeValue] | None
    ) -> dict[str, AttributeValue]:
        result: dict[str, AttributeValue] = {}
        for key, value in (attributes or {}).items():
            if any(word in key.lower() for word in ("authorization", "cookie", "csrf")):
                result[key] = "[Scrubbed]"
            elif isinstance(value, str):
                result[key] = self._text(value)
            elif isinstance(value, Sequence) and all(
                isinstance(item, str) for item in value
            ):
                result[key] = tuple(
                    self._text(item) for item in value if isinstance(item, str)
                )
            else:
                result[key] = value
        return result

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        sanitized = [
            ReadableSpan(
                name=self._text(span.name),
                context=span.context,
                parent=span.parent,
                resource=span.resource,
                kind=span.kind,
                attributes=self._attributes(span.attributes),
                events=[
                    Event(
                        event.name, self._attributes(event.attributes), event.timestamp
                    )
                    for event in span.events
                ],
                links=span.links,
                start_time=span.start_time,
                end_time=span.end_time,
                status=trace.Status(
                    span.status.status_code,
                    self._text(span.status.description)
                    if span.status.description
                    else None,
                ),
                instrumentation_scope=span.instrumentation_scope,
            )
            for span in spans
        ]
        return self.delegate.export(sanitized)

    def shutdown(self) -> None:
        self.delegate.shutdown()


def _create_provider(
    endpoint: str, environment: str, secrets: tuple[str, ...]
) -> TracerProvider:
    from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor
    from phoenix.otel import BatchSpanProcessor, HTTPSpanExporter, register

    provider = register(
        endpoint=f"{endpoint}/v1/traces",
        protocol="http/protobuf",
        project_name=PROJECT_NAME,
        batch=True,
        verbose=False,
        auto_instrument=False,
        set_global_tracer_provider=False,
        sampler=ALWAYS_ON,
        headers={"Content-Type": "application/x-protobuf"},
        resource=Resource(
            {
                "service.name": "abaqus-sim-agent",
                "service.version": version("abaqus-sim-agent"),
                "deployment.environment.name": environment,
            }
        ),
    )
    # Phoenix's documented add_span_processor replaces its default exporter.
    # Replace it once, then order conversion before export; Evals can append its
    # in-memory recorder later without removing our exporter.
    provider.add_span_processor(_IdentityProcessor())
    provider.add_span_processor(OpenInferenceSpanProcessor())
    http = requests.Session()
    http.trust_env = False
    http.max_redirects = 0
    exporter = HTTPSpanExporter(
        endpoint=f"{endpoint}/v1/traces",
        timeout=2,
        headers={"Content-Type": "application/x-protobuf"},
        session=http,
    )
    provider.add_span_processor(
        BatchSpanProcessor(span_exporter=_CredentialExporter(exporter, secrets))
    )
    return provider


def _shutdown(provider: TracerProvider) -> None:
    def close() -> None:
        try:
            provider.shutdown()
        except Exception:
            _logger.warning("Phoenix shutdown failed; some traces may be missing")

    worker = threading.Thread(target=close, name="phoenix-shutdown", daemon=True)
    worker.start()
    worker.join(SHUTDOWN_TIMEOUT_MILLIS / 1000)
    if worker.is_alive():
        _logger.warning("Phoenix shutdown timed out; some traces may be missing")


@contextmanager
def telemetry_session(enabled: bool, *, environment: str) -> Iterator[None]:
    """由入口启动一次 Phoenix 会话；不读取工作区配置，不更改业务运行合同。

    Args:
        enabled: 仅由 --phoenix 显式启用。
        environment: 当前入口用途，例如 cli、web、evaluation。

    Raises:
        ValueError: 本机 collector 地址无效。
        RuntimeError: 当前进程已配置 tracing，或 Phoenix 初始化失败。

    Note:
        只支持无鉴权本机服务。凭据不继承给 Shell/vendor 子进程；缺少服务时
        导出失败不重跑业务。一个进程只初始化一次启用的 tracing 会话。
    """
    global _provider, _settings, _endpoint, _environment, _credential_values
    for key in tuple(os.environ):
        if key in {"PHOENIX_API_KEY", "PHOENIX_CLIENT_HEADERS"} or (
            key.startswith("OTEL_EXPORTER_OTLP") and key.endswith("HEADERS")
        ):
            os.environ.pop(key, None)
    if not enabled:
        yield
        return
    if _provider is not None or not isinstance(
        trace.get_tracer_provider(), trace.ProxyTracerProvider
    ):
        raise RuntimeError("Tracing is already configured in this process")
    endpoint = local_endpoint(
        os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", DEFAULT_ENDPOINT)
    )
    secrets = tuple(
        value
        for key in (
            "DEEPSEEK_API_KEY",
            "OPENCODE_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        )
        if (value := os.environ.get(key))
    )
    from opentelemetry.instrumentation.threading import ThreadingInstrumentor

    instrumentor = ThreadingInstrumentor()
    own_threading = not instrumentor.is_instrumented_by_opentelemetry
    with _sdk_environment(endpoint):
        provider = _create_provider(endpoint, environment, secrets)
        if own_threading:
            instrumentor.instrument()
        trace.set_tracer_provider(provider)
        _provider = provider
        _settings = InstrumentationSettings(
            tracer_provider=provider,
            meter_provider=NoOpMeterProvider(),
            include_content=True,
            include_binary_content=False,
            include_model_request_parameters=False,
        )
        _endpoint = endpoint
        _environment = environment
        _credential_values = secrets
        try:
            yield
        finally:
            _settings = None
            _provider = None
            _environment = None
            _endpoint = None
            _credential_values = ()
            _shutdown(provider)
            if own_threading:
                instrumentor.uninstrument()


def instrumentation_settings() -> InstrumentationSettings | None:
    return _settings


def collector_endpoint() -> str | None:
    return _endpoint


def redact_text(value: str) -> str:
    for secret in _credential_values:
        value = value.replace(secret, "[Scrubbed]")
    return value


def flush() -> bool:
    return _provider is not None and _provider.force_flush(
        timeout_millis=SHUTDOWN_TIMEOUT_MILLIS
    )


def telemetry_metadata() -> dict[str, object]:
    return {
        "enabled": _settings is not None,
        "include_content": _settings is not None,
        "backend": "phoenix",
        "schema_version": SCHEMA_VERSION,
        "phoenix_otel_version": version("arize-phoenix-otel"),
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "project": PROJECT_NAME,
        "endpoint": _endpoint,
        "environment": _environment,
    }


@contextmanager
def span(
    name: str,
    *,
    parent_attributes: Mapping[str, AttributeValue] | None = None,
    cancelled_exceptions: tuple[type[BaseException], ...] = (asyncio.CancelledError,),
    **attributes: AttributeValue,
) -> Iterator[trace.Span]:
    if _provider is None:
        yield trace.INVALID_SPAN
        return
    with _provider.get_tracer("abaqus_sim_agent").start_as_current_span(
        name,
        attributes={**(parent_attributes or {}), **attributes},
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except cancelled_exceptions:
            current.set_attribute("operation.status", "cancelled")
            raise
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(trace.StatusCode.ERROR, type(exc).__name__)
            raise


def set_attributes(**attributes: AttributeValue) -> None:
    if _provider is not None:
        trace.get_current_span().set_attributes(attributes)


@contextmanager
def execution_context(**attributes: AttributeValue) -> Iterator[None]:
    """跨后台 task 传播业务身份，不新建一次实际执行 span。"""
    if _provider is None:
        yield
        return
    ctx = context.get_current()
    for key, value in attributes.items():
        ctx = baggage.set_baggage(key, value, context=ctx)
    token = context.attach(ctx)
    try:
        yield
    finally:
        context.detach(token)


def execution_attributes() -> dict[str, AttributeValue]:
    return {
        key: value
        for key in _ID_KEYS
        if isinstance(value := baggage.get_baggage(key), str)
    }


@contextmanager
def instrument_web(app: FastAPI) -> Iterator[None]:
    if _provider is None:
        yield
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_provider,
        excluded_urls=r"/api/v1/session(?:\?|$),/events(?:\?|$),/assets/,/third-party-notices(?:\?|$)",
        http_capture_headers_server_request=[],
        http_capture_headers_server_response=[],
        http_capture_headers_sanitize_fields=[".*"],
        exclude_spans=["receive", "send"],
    )
    try:
        yield
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
