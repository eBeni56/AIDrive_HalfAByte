"""Time-based rolling windows. Everything is weighted by elapsed time, never by frame count."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class EventLog(Generic[T]):
    """Timestamped events kept for `horizon_s`; count/iterate the ones inside a shorter window."""

    def __init__(self, horizon_s: float) -> None:
        self.horizon_s = horizon_s
        self._items: deque[tuple[float, T]] = deque()

    def clear(self) -> None:
        self._items.clear()

    def add(self, t: float, item: T) -> None:
        self._items.append((t, item))

    def replace_last(self, item: T) -> None:
        if self._items:
            self._items[-1] = (self._items[-1][0], item)

    def prune(self, now: float) -> None:
        while self._items and now - self._items[0][0] > self.horizon_s:
            self._items.popleft()

    def since(self, now: float, window_s: float) -> Iterator[tuple[float, T]]:
        return ((t, it) for t, it in self._items if now - t <= window_s)

    def count(self, now: float, window_s: float) -> int:
        return sum(1 for _ in self.since(now, window_s))


@dataclass(frozen=True, slots=True)
class Sample:
    t: float
    values: tuple[float, ...]


class History:
    """Recent samples of a small vector signal (e.g. yaw/pitch/roll) for trend look-ups."""

    def __init__(self, horizon_s: float) -> None:
        self.horizon_s = horizon_s
        self._samples: deque[Sample] = deque()

    def clear(self) -> None:
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)

    def add(self, t: float, *values: float) -> None:
        self._samples.append(Sample(t, values))
        while self._samples and t - self._samples[0].t > self.horizon_s:
            self._samples.popleft()

    @property
    def latest(self) -> Sample | None:
        return self._samples[-1] if self._samples else None

    def at(self, t: float) -> Sample | None:
        """Sample closest to time t (None if empty or t is older than the history)."""
        if not self._samples or t < self._samples[0].t - 0.25:
            return None
        return min(self._samples, key=lambda s: abs(s.t - t))

    def window(self, now: float, window_s: float) -> list[Sample]:
        return [s for s in self._samples if now - s.t <= window_s]

    def span_s(self) -> float:
        return self._samples[-1].t - self._samples[0].t if len(self._samples) > 1 else 0.0


@dataclass(slots=True)
class _Interval:
    start: float
    end: float
    value: float


class TimeWeightedWindow:
    """Zero-order-hold window over a scalar signal with gaps.

    Each sample closes the interval since the previous sample, which gets the *previous* value.
    `None` values and intervals longer than `max_gap_s` are gaps (counted as neither).
    """

    def __init__(self, window_s: float, max_gap_s: float) -> None:
        self.window_s = window_s
        self.max_gap_s = max_gap_s
        self._intervals: deque[_Interval] = deque()
        self._prev: tuple[float, float | None] | None = None

    def clear(self) -> None:
        self._intervals.clear()
        self._prev = None

    def add(self, t: float, value: float | None) -> None:
        if self._prev is not None:
            t0, v0 = self._prev
            if v0 is not None and 0 < t - t0 <= self.max_gap_s:
                self._intervals.append(_Interval(t0, t, v0))
        self._prev = (t, value)
        while self._intervals and t - self._intervals[0].end > self.window_s:
            self._intervals.popleft()

    def coverage_s(self) -> float:
        return sum(i.end - i.start for i in self._intervals)

    def fraction_at_or_below(self, threshold: float) -> float | None:
        total = self.coverage_s()
        if total <= 0:
            return None
        below = sum(i.end - i.start for i in self._intervals if i.value <= threshold)
        return below / total

    def mean_above(self, threshold: float) -> float | None:
        """Time-weighted mean of the values strictly above `threshold` (e.g. openness of non-closed eyes)."""
        num = den = 0.0
        for i in self._intervals:
            if i.value > threshold:
                d = i.end - i.start
                num += i.value * d
                den += d
        return num / den if den > 0 else None


class Debounce:
    """A boolean condition that must hold `on_s` to switch on and fail `off_s` to switch off.

    `since` is when the condition started being true for the current run (used as an episode start time).
    """

    def __init__(self, on_s: float, off_s: float) -> None:
        self.on_s, self.off_s = on_s, off_s
        self.active = False
        self.since: float | None = None  # start of the current true-run
        self._false_since: float | None = None

    def reset(self) -> None:
        self.active = False
        self.since = None
        self._false_since = None

    def update(self, condition: bool, t: float) -> str | None:
        """Returns "on" when it switches on, "off" when it switches off, else None."""
        if condition:
            self._false_since = None
            if self.since is None:
                self.since = t
            if not self.active and t - self.since >= self.on_s:
                self.active = True
                return "on"
            return None
        if not self.active:
            self.since = None
            return None
        if self._false_since is None:
            self._false_since = t
        if t - self._false_since >= self.off_s:
            self.active = False
            self.since = None
            self._false_since = None
            return "off"
        return None

    def held_for(self, t: float) -> float:
        return t - self.since if self.since is not None else 0.0
