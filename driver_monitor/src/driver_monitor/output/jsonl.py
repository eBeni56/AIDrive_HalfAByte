"""Writes every driver_state message as one JSON line (replayable, easy to load with pandas)."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from driver_monitor.output.schema import DriverState


class JsonlWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f: TextIO = self.path.open("w", encoding="utf-8")
        self._n = 0

    def write(self, state: DriverState) -> None:
        self._f.write(state.model_dump_json())
        self._f.write("\n")
        self._n += 1
        if self._n % 30 == 0:
            self._f.flush()

    def close(self) -> None:
        self._f.close()
