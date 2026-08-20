"""Forecast run entrypoint for the Cloud Run Job (see
infra/modules/app/cloud_run_jobs.tf).

Orchestrates one forecast run: builds its run-lineage record and persists
it. Everything logged during the run -- this module, engine/ calls it
makes, db writes -- carries the run's `run_id` via
`backend.observability.run_id_scope`, so a single grep on run_id
reconstructs the full trail for any historical run (CLAUDE.md invariant
9: a run must be reproducible from its lineage alone).

This module owns orchestration and persistence only; it must never
reimplement forecasting math -- see CLAUDE.md "Repository layout". All
computation is delegated to engine/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import date
from importlib.metadata import version as pkg_version

from opentelemetry import trace

from backend.db.base import get_session_factory
from backend.db.models.enums import PeriodGrain
from backend.db.models.forecast import ForecastRun
from backend.observability import configure_logging, configure_tracing, run_id_scope

logger = logging.getLogger(__name__)


def config_hash(config: dict[str, object]) -> str:
    """Deterministic hash of the run's config for lineage: same
    config_hash + engine_version + git_sha + input_data_versions must
    reproduce the same run."""
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_forecast_run(
    *,
    kernel_version: str,
    book_snapshot_date: date,
    period_grain: PeriodGrain,
    config: dict[str, object],
    input_data_versions: dict[str, str],
    random_seed: int | None = None,
) -> ForecastRun:
    """Construct (but do not persist) the ForecastRun row for this
    execution, stamped with full lineage: engine version, config hash,
    git SHA, and input dataset versions."""
    return ForecastRun(
        id=uuid.uuid4(),
        kernel_version=kernel_version,
        book_snapshot_date=book_snapshot_date,
        period_grain=period_grain,
        random_seed=random_seed,
        engine_version=pkg_version("engine"),
        config_hash=config_hash(config),
        git_sha=os.environ.get("GIT_SHA", "unknown"),
        input_data_versions=input_data_versions,
    )


def execute(forecast_run: ForecastRun) -> None:
    """Run and persist one forecast run. Split out from `build_forecast_run`
    so callers (and tests) can inspect the lineage before it's committed.
    """
    with run_id_scope(str(forecast_run.id)):
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("forecast_run") as span:
            span.set_attribute("run_id", str(forecast_run.id))
            span.set_attribute("kernel_version", forecast_run.kernel_version)
            span.set_attribute("engine_version", forecast_run.engine_version)
            logger.info(
                "forecast run starting",
                extra={
                    "kernel_version": forecast_run.kernel_version,
                    "engine_version": forecast_run.engine_version,
                    "book_snapshot_date": forecast_run.book_snapshot_date.isoformat(),
                    "config_hash": forecast_run.config_hash,
                    "git_sha": forecast_run.git_sha,
                },
            )

            # Forecasting math (engine.simulate/kernel/hierarchy calls that
            # populate ForecastValue rows) is orchestrated here in later
            # work; this entrypoint currently establishes the run and its
            # lineage record, which is the durable, reproducible anchor
            # every later forecast_value row hangs off of.
            session_factory = get_session_factory()
            with session_factory() as session:
                session.add(forecast_run)
                session.commit()

            logger.info("forecast run persisted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute a forecast run and persist its lineage.")
    parser.add_argument("--kernel-version", required=True)
    parser.add_argument("--book-snapshot-date", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--period-grain",
        choices=[grain.value for grain in PeriodGrain],
        default=PeriodGrain.WEEK.value,
    )
    parser.add_argument("--config", type=str, default="{}", help="JSON object of run configuration")
    parser.add_argument(
        "--input-data-versions",
        type=str,
        default="{}",
        help="JSON object mapping input dataset name to version/snapshot id",
    )
    args = parser.parse_args(argv)

    configure_logging()
    configure_tracing("forecast-job")

    forecast_run = build_forecast_run(
        kernel_version=args.kernel_version,
        book_snapshot_date=args.book_snapshot_date,
        period_grain=PeriodGrain(args.period_grain),
        config=json.loads(args.config),
        input_data_versions=json.loads(args.input_data_versions),
    )
    execute(forecast_run)
    print(forecast_run.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
