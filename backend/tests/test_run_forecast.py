from datetime import date
from decimal import Decimal

from sqlalchemy import select

from backend.db.base import get_session_factory
from backend.db.models.enums import PeriodGrain
from backend.db.models.forecast import ForecastRun
from backend.jobs.run_forecast import build_forecast_run, config_hash, execute


def test_config_hash_is_stable_regardless_of_key_order() -> None:
    a = config_hash({"x": 1, "y": Decimal("2")})
    b = config_hash({"y": Decimal("2"), "x": 1})
    assert a == b


def test_config_hash_differs_for_different_config() -> None:
    assert config_hash({"x": 1}) != config_hash({"x": 2})


def test_build_forecast_run_stamps_lineage() -> None:
    run = build_forecast_run(
        kernel_version="kernel-v1",
        book_snapshot_date=date(2026, 1, 5),
        period_grain=PeriodGrain.WEEK,
        config={"scenario": "base"},
        input_data_versions={"orders": "2026-01-05"},
    )
    assert run.engine_version
    assert run.git_sha
    assert run.config_hash == config_hash({"scenario": "base"})
    assert run.input_data_versions == {"orders": "2026-01-05"}


def test_execute_persists_the_run() -> None:
    run = build_forecast_run(
        kernel_version="kernel-v1",
        book_snapshot_date=date(2026, 1, 5),
        period_grain=PeriodGrain.WEEK,
        config={"scenario": "base"},
        input_data_versions={},
    )
    execute(run)

    session_factory = get_session_factory()
    with session_factory() as session:
        persisted = session.scalar(select(ForecastRun).where(ForecastRun.id == run.id))
    assert persisted is not None
    assert persisted.engine_version == run.engine_version
    assert persisted.git_sha == run.git_sha
