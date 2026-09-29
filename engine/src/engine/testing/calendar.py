"""Calendar-date <-> integer Period mapping (month grain) for engine.testing's
YAML config layer.

`engine.domain.Period` stays a plain, wall-clock-free integer index
everywhere in engine's production code (base.py, kernel.py, hierarchy.py)
per CLAUDE.md's "periods are discrete and ordered... never wall-clock
timestamps" convention. Calendar dates exist only here, at the YAML config
boundary: a config's `meta.history_start` becomes Period(0), and every
other date in the file is converted to an integer offset from it before
anything else in engine.testing ever sees it.

Month grain only -- the only grain any config uses today (`period_grain:
month`). Not generalized to week/day grains until something needs one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from engine.domain import Period

_MONTH_ABBREVIATIONS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)


def parse_month(value: str | date) -> date:
    """Parse a "YYYY-MM" or "YYYY-MM-DD" string (or pass a date through)
    to the first day of that month."""
    if isinstance(value, date):
        return value.replace(day=1)
    year_str, month_str, *_ = str(value).split("-")
    return date(int(year_str), int(month_str), 1)


def month_abbreviation(month: int) -> str:
    """1 (January) .. 12 (December) -> "jan".."dec", matching the keys a
    config's seasonality tables use."""
    return _MONTH_ABBREVIATIONS[month - 1]


@dataclass(frozen=True, slots=True)
class MonthlyCalendar:
    """Maps calendar months to/from integer Period indices, anchored so
    `epoch` (a config's meta.history_start) is Period(0)."""

    epoch: date  # always the 1st of a month

    def period_of(self, value: str | date) -> Period:
        d = parse_month(value)
        months = (d.year - self.epoch.year) * 12 + (d.month - self.epoch.month)
        return Period(months)

    def date_of(self, period: Period) -> date:
        total_months = (self.epoch.year * 12 + (self.epoch.month - 1)) + int(period)
        year, month0 = divmod(total_months, 12)
        return date(year, month0 + 1, 1)

    def month_of_year(self, period: Period) -> int:
        """1 (January) .. 12 (December)."""
        return self.date_of(period).month

    def days_in_month(self, period: Period) -> int:
        start = self.date_of(period)
        end = self.date_of(Period(int(period) + 1))
        return (end - start).days

    def working_days(self, period: Period) -> int:
        """Weekday (Mon-Fri) count for the calendar month `period` falls
        in. No holiday calendar -- an explicit simplification, not an
        attempt at a real business-day count; easy to extend with a
        holiday list later if a scenario needs one."""
        start = self.date_of(period)
        return sum(
            1
            for offset in range(self.days_in_month(period))
            if (start + timedelta(days=offset)).weekday() < 5
        )

    def day_offset_to_period(
        self, raise_period: Period, day_offset_in_month: int, days_later: float
    ) -> Period:
        """The period containing (the `day_offset_in_month`-th day of
        `raise_period`'s calendar month) + `days_later` days. Used to
        discretize a continuous cycle-time-in-days draw into an integer
        period-age -- see engine.testing.synthetic's kernel derivation."""
        start = self.date_of(raise_period) + timedelta(days=day_offset_in_month)
        close_date = start + timedelta(days=days_later)
        # Re-derive the period index from the resulting date by counting
        # month boundaries crossed, rather than parsing a "YYYY-MM"
        # string -- close_date may fall on any day of its month.
        months = (close_date.year - self.epoch.year) * 12 + (close_date.month - self.epoch.month)
        return Period(months)
