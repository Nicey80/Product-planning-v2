"""FastAPI application entry point.

This service is a thin HTTP layer over engine/. It must never reimplement
forecasting math -- see CLAUDE.md "Repository layout".
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation

from engine.domain import ChannelId, KernelSegment, NodeId, TxnType
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from backend.db.base import get_db_engine
from backend.observability import bind_context, configure_logging, configure_tracing

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Subscription Forecasting API", version="0.1.0")
configure_tracing("backend-api", app)


@app.middleware("http")
async def request_context(request: Request, call_next: object) -> Response:
    """Bind a request_id to the logging context for the life of the
    request, so every log line emitted while handling it -- this module,
    db access, anything else -- can be correlated back to it."""
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    with bind_context(request_id=request_id):
        response: Response = await call_next(request)  # type: ignore[operator]
        response.headers["x-request-id"] = request_id
        return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness: the process can actually serve traffic -- i.e. its
    database connection works. Cloud Run uses this to gate traffic to a
    new revision (see infra/modules/app/cloud_run.tf)."""
    try:
        with get_db_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="not ready") from exc
    return {"status": "ready"}


class KernelValidationRequest(BaseModel):
    node: str
    order_channel: str
    txn_type: TxnType
    g: list[str]
    breakage: str


class KernelValidationResponse(BaseModel):
    valid: bool
    detail: str | None = None


@app.post("/api/v1/kernel/validate", response_model=KernelValidationResponse)
def validate_kernel(payload: KernelValidationRequest) -> KernelValidationResponse:
    """Validate that a proposed closure kernel satisfies invariant 2:
    sum(g) + breakage == 1. Delegates entirely to engine.KernelSegment.
    """
    try:
        g = tuple(Decimal(value) for value in payload.g)
        breakage = Decimal(payload.breakage)
    except InvalidOperation as exc:
        raise HTTPException(status_code=422, detail="g and breakage must be decimals") from exc

    try:
        KernelSegment(
            node=NodeId(payload.node),
            order_channel=ChannelId(payload.order_channel),
            txn_type=payload.txn_type,
            g=g,
            breakage=breakage,
        )
    except ValueError as exc:
        return KernelValidationResponse(valid=False, detail=str(exc))

    return KernelValidationResponse(valid=True)
