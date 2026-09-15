"""OpenTelemetry spans with bounded metadata and no exception payload recording."""

from opentelemetry.sdk.trace import TracerProvider
from contextlib import contextmanager
from functools import wraps
from time import monotonic

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .logging import bind_context, get_context


class TraceManager:
    def __init__(self, provider=None):
        self.tracer = trace.get_tracer("agent_adapter_service", tracer_provider=provider)

    @contextmanager
    def span(self, name: str, attributes=None, *, parent=None):
        attributes = {
            k: v
            for k, v in (attributes or {}).items()
            if k in {"request_id", "thread_id", "run_id", "trace_id", "surface", "operation"}
            and isinstance(v, str)
            and len(v) <= 255
        }
        with self.tracer.start_as_current_span(
            name,
            context=parent,
            attributes={**get_context(), **attributes},
            record_exception=False,
            set_status_on_exception=False,
        ) as current:
            context = current.get_span_context()
            fields = {"trace_id": format(context.trace_id, "032x")} if context.is_valid else {}
            with bind_context(**fields):
                try:
                    yield current
                except BaseException:
                    current.set_status(Status(StatusCode.ERROR))
                    raise

    def inject(self) -> dict[str, str]:
        carrier: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(carrier)
        return carrier

    def extract(self, carrier):
        return TraceContextTextMapPropagator().extract(carrier)


# A local SDK provider produces real IDs even without an exporter. Hosts may add
# processors or inject their provider without changing the process-global provider.

provider = TracerProvider()
traces = TraceManager(provider)
span = traces.span


def observed(surface: str, operation: str):
    """Instrument an async boundary before it normalizes upstream exceptions."""

    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            from .metrics import metrics

            started, status = monotonic(), "success"
            try:
                with span(
                    f"{surface}.{operation}", {"surface": surface, "operation": operation}
                ) as current:
                    result = await function(*args, **kwargs)
                    if (
                        isinstance(result, dict)
                        and result.get("ok") is False
                        or getattr(result, "status", "success") != "success"
                    ):
                        status = "failure"
                        current.set_status(Status(StatusCode.ERROR))
                    return result
            except BaseException as exc:
                status = "cancelled" if type(exc).__name__ == "CancelledError" else "failure"
                raise
            finally:
                metrics.record(surface, operation, monotonic() - started, status)

        return wrapped

    return decorate
