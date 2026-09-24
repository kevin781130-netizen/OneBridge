from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit


def _safe_attributes(
    attributes: dict[str, Any] | None,
) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for key, value in (attributes or {}).items():
        name = str(key)[:120]
        if isinstance(value, (bool, int, float)):
            result[name] = value
        elif value is not None:
            result[name] = str(value)[:500]
    return result


def _parse_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    headers: dict[str, str] = {}
    for item in raw.split(","):
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError("ONEBRIDGE_OTEL_HEADERS must use key=value pairs")
        headers[key.strip()] = value.strip()
    return headers


def _signal_endpoint(base: str, signal: str) -> str:
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ONEBRIDGE_OTEL_ENDPOINT must be an HTTP(S) URL")
    path = parsed.path.rstrip("/")
    if path.endswith(f"/v1/{signal}"):
        final_path = path
    elif path.endswith("/v1/traces") or path.endswith("/v1/metrics"):
        final_path = path.rsplit("/v1/", 1)[0] + f"/v1/{signal}"
    else:
        final_path = path + f"/v1/{signal}"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, final_path, parsed.query, "")
    )


@dataclass(slots=True)
class Telemetry:
    enabled: bool = False
    tracer: Any = None
    meter: Any = None
    _counters: dict[str, Any] = field(default_factory=dict)
    _histograms: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def disabled(cls) -> "Telemetry":
        return cls(enabled=False)

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[Any]:
        if not self.enabled or self.tracer is None:
            yield None
            return
        with self.tracer.start_as_current_span(str(name)) as span:
            for key, value in _safe_attributes(attributes).items():
                span.set_attribute(key, value)
            yield span

    def count(
        self,
        name: str,
        amount: int = 1,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled or self.meter is None:
            return
        counter = self._counters.get(name)
        if counter is None:
            counter = self.meter.create_counter(name)
            self._counters[name] = counter
        counter.add(int(amount), _safe_attributes(attributes))

    def record(
        self,
        name: str,
        value: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled or self.meter is None:
            return
        histogram = self._histograms.get(name)
        if histogram is None:
            histogram = self.meter.create_histogram(name)
            self._histograms[name] = histogram
        histogram.record(float(value), _safe_attributes(attributes))


def build_telemetry(
    *,
    service_name: str,
    endpoint: str | None,
    headers: str | None = None,
) -> Telemetry:
    if not endpoint:
        return Telemetry.disabled()

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise RuntimeError(
            "OpenTelemetry is configured but the 'telemetry' extra is not installed"
        ) from exc

    resource = Resource.create({
        "service.name": str(service_name or "onebridge"),
        "service.version": "0.1.0",
    })
    parsed_headers = _parse_headers(headers)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=_signal_endpoint(endpoint, "traces"),
                headers=parsed_headers,
            )
        )
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=_signal_endpoint(endpoint, "metrics"),
            headers=parsed_headers,
        )
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    return Telemetry(
        enabled=True,
        tracer=trace.get_tracer("onebridge.control_plane", "0.1.0"),
        meter=metrics.get_meter("onebridge.control_plane", "0.1.0"),
    )
