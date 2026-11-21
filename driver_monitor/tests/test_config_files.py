"""Every settings file in config/ must load: a key that the code no longer knows is an error."""

from __future__ import annotations

from pathlib import Path

import pytest

from driver_monitor.config import load_config

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize("path", sorted(CONFIG_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_config_file_loads(path: Path) -> None:
    load_config(path)
