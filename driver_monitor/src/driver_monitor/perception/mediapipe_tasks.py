"""What the two MediaPipe tasks (face landmarker, phone finder) share: the model file and the timestamps."""

from __future__ import annotations

from pathlib import Path


def model_file(path: str, base_dir: Path | None, what: str) -> Path:
    """The model file (relative paths are relative to `base_dir`). FileNotFoundError if it is missing."""
    model = Path(path)
    if not model.is_absolute() and base_dir is not None:
        model = base_dir / model
    if not model.exists():
        raise FileNotFoundError(f"{what} model not found at {model}. Run: uv run python tools/download_model.py")
    return model


class Timestamps:
    """Milliseconds for MediaPipe's VIDEO / LIVE_STREAM modes, which need strictly increasing timestamps."""

    def __init__(self) -> None:
        self._last_ms = -1

    def ms(self, t_s: float) -> int:
        self._last_ms = max(round(t_s * 1000.0), self._last_ms + 1)
        return self._last_ms
