"""OpenTelemetry instruments; IDs and user-controlled strings are never labels."""

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from contextlib import contextmanager
from time import monotonic

from opentelemetry import metrics as otel_metrics


class Metrics:
    def __init__(self, provider=None):
        meter = otel_metrics.get_meter("agent_adapter_service", meter_provider=provider)
        self.calls = meter.create_counter("adapter.calls", unit="{call}")
        self.errors = meter.create_counter("adapter.errors", unit="{error}")
        self.latency = meter.create_histogram("adapter.duration", unit="s")

    def record(self, surface: str, operation: str, duration: float, status: str):
        # Only fixed integration categories are labels. Tool names belong in spans/audit.
        surface = surface if surface in {"run", "tool", "mcp", "saleor", "parlant"} else "other"
        status = status if status in {"success", "failure", "cancelled"} else "failure"
        labels = {"surface": surface, "status": status}
        self.calls.add(1, labels)
        self.latency.record(max(0, duration), labels)
        if status != "success":
            self.errors.add(1, labels)

    @contextmanager
    def measure(self, surface: str, operation: str):
        started, status = monotonic(), "success"
        try:
            yield
        except BaseException as exc:
            status = (
                "cancelled"
                if type(exc).__name__ in {"CancelledError", "GeneratorExit"}
                else "failure"
            )
            raise
        finally:
            self.record(surface, operation, monotonic() - started, status)


reader = InMemoryMetricReader()
provider = MeterProvider(metric_readers=[reader])
metrics = Metrics(provider)
