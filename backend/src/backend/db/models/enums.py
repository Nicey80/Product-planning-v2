"""Persistence-layer enums.

`TxnType` is imported from `engine.domain` rather than redefined here --
it is a glossary term with a single source of truth (see CLAUDE.md
"Terminology discipline"). The enums below are persistence-only concepts
that engine/ has no reason to know about.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum

__all__ = [
    "Stage",
    "PeriodGrain",
    "ApprovalStatus",
    "ApprovalSubjectType",
    "AuditAction",
    "OverlayType",
    "pg_enum",
]


def pg_enum(enum_cls: type[StrEnum], *, name: str) -> SAEnum:
    """A native Postgres ENUM column type that stores the StrEnum's
    lowercase `.value` (e.g. "acquisition") rather than SQLAlchemy's
    default of the Python member name (e.g. "ACQUISITION") -- so DB
    values match glossary terms verbatim, consistent with warehouse/ SQL.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Stage(StrEnum):
    """Pipeline stage a forecast_value row represents."""

    RAISED = "raised"
    CLOSED = "closed"
    BROKEN = "broken"
    BOOK = "book"
    BASE = "base"


class PeriodGrain(StrEnum):
    """Discrete period grain a forecast_run's periods are keyed at."""

    WEEK = "week"
    MONTH = "month"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalSubjectType(StrEnum):
    """What an approval record is deciding on. `subject_id` on `approval`
    points at the row identified by this type; there is deliberately no
    FK for it since the referent table varies (polymorphic reference)."""

    OVERLAY = "overlay"
    FORECAST_RUN = "forecast_run"


class AuditAction(StrEnum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"


class OverlayType(StrEnum):
    """What kind of overlay a row represents.

    MIGRATION_ACQ/MIGRATION_CHURN are the paired, portfolio-internal base
    overlays described in docs/domain-model.md section 5.2 (invariant 8:
    they bypass the order pipeline entirely). MANUAL_ADJUSTMENT is a
    planner override of a model_value on top of the raise/close/break/
    book/base pipeline.
    """

    MIGRATION_ACQ = "migration_acq"
    MIGRATION_CHURN = "migration_churn"
    MANUAL_ADJUSTMENT = "manual_adjustment"
