"""Product and channel hierarchy dimensions, SCD2-versioned.

Each row is one version of one leaf (`node` / `sub_channel`) hierarchy
membership, valid over `[valid_from, valid_to)`. `valid_to is null`
(equivalently `is_current`) marks the currently-effective version. A
partial unique index enforces exactly one current row per leaf.

Fact tables (forecast_value, overlay, kernel_validation) FK to the
surrogate `id` of the dimension row current at the time the fact was
written, so a later hierarchy reorganization does not retroactively
rewrite history it wasn't part of.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class DimProduct(Base):
    """Product hierarchy: product_group -> product -> node (leaf variant)."""

    __tablename__ = "dim_product"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node: Mapped[str] = mapped_column(String, nullable=False)
    product: Mapped[str] = mapped_column(String, nullable=False)
    product_group: Mapped[str] = mapped_column(String, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("node", "valid_from", name="uq_dim_product_node_valid_from"),
        Index(
            "uq_dim_product_node_current",
            "node",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class DimChannel(Base):
    """Channel hierarchy: channel_group -> channel -> sub_channel (leaf).

    `sub_channel` is the ChannelId value used elsewhere as both
    order_channel and acquisition_channel (see CLAUDE.md glossary)."""

    __tablename__ = "dim_channel"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sub_channel: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    channel_group: Mapped[str] = mapped_column(String, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("sub_channel", "valid_from", name="uq_dim_channel_sub_channel_valid_from"),
        Index(
            "uq_dim_channel_sub_channel_current",
            "sub_channel",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
