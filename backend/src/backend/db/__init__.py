"""Postgres persistence layer (SQLAlchemy 2.0 models + Alembic migrations).

Schema only in this package -- no forecasting math (see CLAUDE.md "Engine
purity"; engine/ owns all forecast computation). This layer stores
forecast_run/forecast_value outputs, dimension SCD2 history, overlays,
approvals, and audit trails for the backend API to read and write.
"""

from __future__ import annotations
