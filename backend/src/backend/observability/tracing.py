"""OpenTelemetry trace setup for the API and the forecast job.

Exports to an OTLP/gRPC collector when `OTEL_EXPORTER_OTLP_ENDPOINT` is
set (the normal case in every deployed environment -- see infra/modules
/app/cloud_run.tf for the collector sidecar/endpoint wiring). With no
endpoint configured (local dev, unit tests) spans are still created and
attributed but never exported, so nothing here requires a collector to
be reachable.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_tracing(service_name: str, app: FastAPI | None = None) -> TracerProvider:
    """Install a TracerProvider for `service_name` and, if `app` is
    given, instrument it for automatic request-span creation. Returns
    the provider so callers (e.g. the forecast job) can create their own
    spans via `trace.get_tracer(__name__)`.
    """
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: os.environ.get("GIT_SHA", "unknown"),
            "deployment.environment": os.environ.get("ENVIRONMENT", "dev"),
        }
    )
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))

    trace.set_tracer_provider(provider)

    if app is not None:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    return provider
