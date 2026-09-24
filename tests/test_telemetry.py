from onebridge.telemetry import Telemetry, _signal_endpoint


def test_disabled_telemetry_is_noop():
    telemetry = Telemetry.disabled()
    with telemetry.span("test", {"secret": "not-exported"}) as span:
        assert span is None
    telemetry.count("counter")
    telemetry.record("histogram", 1.5)


def test_signal_endpoint_appends_otlp_paths():
    assert (
        _signal_endpoint("https://otel.example.com", "traces")
        == "https://otel.example.com/v1/traces"
    )
    assert (
        _signal_endpoint("https://otel.example.com/base", "metrics")
        == "https://otel.example.com/base/v1/metrics"
    )
    assert (
        _signal_endpoint(
            "https://otel.example.com/base/v1/traces",
            "metrics",
        )
        == "https://otel.example.com/base/v1/metrics"
    )
