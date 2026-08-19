"""Overlay, approval, kernel validation, audit log, adjustment log.

These tables record the human-in-the-loop layer around a forecast run:
manual/migration overlays, their approval workflow, persisted kernel
contract checks (invariant 2), a generic audit trail, and a log of
point-value adjustments to individual forecast_value cells.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from engine.domain import TxnType
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base
from backend.db.models.enums import (
    ApprovalStatus,
    ApprovalSubjectType,
    AuditAction,
    OverlayType,
    Stage,
    pg_enum,
)

_VALUE = Numeric(18, 6)


class Overlay(Base):
    """A manual or migration overlay entry targeting one forecast_value
    cell's grain. `run_id` is nullable so a standing overlay (e.g. a
    planned migration schedule) can exist before it's attached to a
    specific run."""

    __tablename__ = "overlay"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forecast_run.id", ondelete="CASCADE"), nullable=True
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
    overlay_type: Mapped[OverlayType] = mapped_column(
        pg_enum(OverlayType, name="overlay_type"), nullable=False
    )
    delta_value: Mapped[Decimal] = mapped_column(_VALUE, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Approval(Base):
    """Approval workflow for an overlay or a forecast_run. `subject_id`
    is a polymorphic reference (see ApprovalSubjectType) -- no FK, since
    the referent table depends on subject_type."""

    __tablename__ = "approval"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_type: Mapped[ApprovalSubjectType] = mapped_column(
        pg_enum(ApprovalSubjectType, name="approval_subject_type"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        pg_enum(ApprovalStatus, name="approval_status"),
        nullable=False,
        server_default=ApprovalStatus.PENDING.value,
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class KernelValidation(Base):
    """Persisted record of a closure-kernel contract check (invariant 2:
    sum(g) + breakage == 1) for one node x order_channel x txn_type
    segment, mirroring the /api/v1/kernel/validate check."""

    __tablename__ = "kernel_validation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forecast_run.id", ondelete="CASCADE"), nullable=True
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dim_product.id"), nullable=False
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dim_channel.id"), nullable=False
    )
    txn_type: Mapped[TxnType] = mapped_column(pg_enum(TxnType, name="txn_type"), nullable=False)
    g: Mapped[list[Decimal]] = mapped_column(ARRAY(Numeric(18, 9)), nullable=False)
    breakage: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    """Generic before/after audit trail across mutable tables (overlay,
    approval, ...). Immutable tables like forecast_run/forecast_value are
    write-once and don't need audit rows for updates that can't happen."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    record_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        pg_enum(AuditAction, name="audit_action"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)


class AdjustmentLog(Base):
    """Point-value adjustment history for a single forecast_value cell --
    narrower than audit_log, purpose-built for "who changed this number
    and from what to what"."""

    __tablename__ = "adjustment_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    forecast_value_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forecast_value.id", ondelete="CASCADE"), nullable=False
    )
    previous_value: Mapped[Decimal | None] = mapped_column(_VALUE, nullable=True)
    new_value: Mapped[Decimal] = mapped_column(_VALUE, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjusted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    adjusted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
