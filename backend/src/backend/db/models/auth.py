"""Users, roles, and scopes.

A user's access is scoped by both product group and channel: a
`user_scope` row grants a role over a `(product_group, channel_group)`
slice of the hierarchy, with either side nullable to mean "all". A user
can hold multiple scopes (e.g. planner on group A/channel X and viewer
everywhere else).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("name", name="uq_roles_name"),)


class UserRole(Base):
    """Coarse role grant, independent of the product/channel scoping in
    `user_scope`. Kept separate so "admin" can be a plain global grant
    without needing a scope row."""

    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserScope(Base):
    """Grants `role_id` to `user_id` restricted to a `product_group` and/or
    `channel_group` slice of the hierarchy. Both null means unrestricted
    within that axis. Values are matched against `dim_product.product_group`
    / `dim_channel.channel_group` by the application layer, not by FK --
    those are roll-up levels, not rows of their own dimension table."""

    __tablename__ = "user_scope"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    product_group: Mapped[str | None] = mapped_column(String, nullable=True)
    channel_group: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            "product_group",
            "channel_group",
            name="uq_user_scope_grant",
        ),
    )
