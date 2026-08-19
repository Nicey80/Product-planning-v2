"""All ORM models, imported here so `Base.metadata` is fully populated
for Alembic autogenerate and `Base.metadata.create_all` alike."""

from __future__ import annotations

from backend.db.models.auth import Role, User, UserRole, UserScope
from backend.db.models.dimensions import DimChannel, DimProduct
from backend.db.models.forecast import ForecastRun, ForecastValue
from backend.db.models.governance import (
    AdjustmentLog,
    Approval,
    AuditLog,
    KernelValidation,
    Overlay,
)

__all__ = [
    "User",
    "Role",
    "UserRole",
    "UserScope",
    "DimProduct",
    "DimChannel",
    "ForecastRun",
    "ForecastValue",
    "Overlay",
    "Approval",
    "KernelValidation",
    "AuditLog",
    "AdjustmentLog",
]
