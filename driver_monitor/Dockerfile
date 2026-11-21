FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# mediapipe and OpenCV need these system libraries even without a window (runs on the CPU).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libgles2 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
COPY tools ./tools
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
# models/ and recordings/ are mounted by compose.yml (not baked into the image).
CMD ["python", "-m", "driver_monitor", "--no-window", "--scroll", "--config", "config/driver_monitor.docker.yaml"]
