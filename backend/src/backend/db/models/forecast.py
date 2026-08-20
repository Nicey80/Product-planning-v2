"""Forecast run and forecast value.

`ForecastRun` is write-once at the application layer (invariant 9 in
CLAUDE.md: immutable once created; a correction is a new run, never a
mutation). The schema does not enforce immutability itself -- that is
business logic, out of scope here -- but nothing on this table is
designed to be updated after insert.

`engine_version`, `config_hash`, `git_sha`, and `input_data_versions`
are the run-lineage fields: together with `kernel_version` and
`book_snapshot_date` they let any historical run be reproduced exactly
(pin the same engine release, config, inputs, and code at that git SHA).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from engine.domain import TxnType
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base
from backend.db.models.enums import PeriodGrain, Stage, pg_enum

_VALUE = Numeric(18, 6)


class ForecastRun(Base):
    """A single, versioned, timestamped engine execution over a scenario/
    assumption set. See docs/domain-model.md section 7."""

    __tablename__ = "forecast_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    kernel_version: Mapped[str] = mapped_column(String, nullable=False)
    book_snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_grain: Mapped[PeriodGrain] = mapped_column(
        pg_enum(PeriodGrain, name="period_grain"), nullable=False
    )
    random_seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    engine_version: Mapped[str] = mapped_column(String, nullable=False)
    config_hash: Mapped[str] = mapped_column(String, nullable=False)
    git_sha: Mapped[str] = mapped_column(String, nullable=False)
    input_data_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ForecastValue(Base):
    """One (run, node, channel, period, txn_type, stage) forecast cell.

    `model_value` is the raw engine output; `overlay_value` is the net
    overlay applied on top (nullable -- no overlay touched this cell);
    `final_value` is what's actually reported (model_value adjusted by
    overlay_value). p10/p50/p90 are optional uncertainty bands.
    """

    __tablename__ = "forecast_value"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forecast_run.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dim_product.id"), nullable=False
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dim_channel.id"), nullable=True
    )
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    txn_type: Mapped[TxnType | None] = mapped_column(
        pg_enum(TxnType, name="txn_type"), nullable=True
    )
    stage: Mapped[Stage] = mapped_column(pg_enum(Stage, name="forecast_stage"), nullable=False)
    model_value: Mapped[Decimal] = mapped_column(_VALUE, nullable=False)
    overlay_value: Mapped[Decimal | None] = mapped_column(_VALUE, nullable=True)
    final_value: Mapped[Decimal] = mapped_column(_VALUE, nullable=False)
    p10: Mapped[Decimal | None] = mapped_column(_VALUE, nullable=True)
    p50: Mapped[Decimal | None] = mapped_column(_VALUE, nullable=True)
    p90: Mapped[Decimal | None] = mapped_column(_VALUE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "node_id",
            "channel_id",
            "period",
            "txn_type",
            "stage",
            name="uq_forecast_value_grain",
        ),
    )
