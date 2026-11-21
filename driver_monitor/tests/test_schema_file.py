"""docs/driver_state.schema.json must match the pydantic model (the simulator team builds against it)."""

from __future__ import annotations

import json
from pathlib import Path

from driver_monitor.output.schema import DriverState

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "docs" / "driver_state.schema.json"


def test_schema_file_is_up_to_date() -> None:
    on_disk = json.loads(SCHEMA_FILE.read_text(encoding="utf-8-sig"))  # tolerate a BOM (PowerShell Out-File)
    assert on_disk == DriverState.model_json_schema(), (
        "docs/driver_state.schema.json is outdated - regenerate it: uv run python -m driver_monitor "
        "--print-schema | Out-File -Encoding utf8 docs/driver_state.schema.json"
    )
