"""Forecast run container (invariant 9: immutable once created).

A ForecastRun is write-once: its inputs and outputs are captured at
construction, wrapped in read-only mappings, and never mutated afterward.
A correction is always a new ForecastRun with a new run_id, never an
in-place edit of an existing one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ForecastRun:
    run_id: str
    created_at: str
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]

    def __post_init__(self) -> None:
        # Normalize to a read-only mapping so callers can't mutate the run
        # through a reference to its inputs/outputs after construction.
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))
        object.__setattr__(self, "outputs", MappingProxyType(dict(self.outputs)))
