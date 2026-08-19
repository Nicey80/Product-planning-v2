"""FastAPI application entry point.

This service is a thin HTTP layer over engine/. It must never reimplement
forecasting math -- see CLAUDE.md "Repository layout".
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from engine.domain import ChannelId, KernelSegment, NodeId, TxnType
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Subscription Forecasting API", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
