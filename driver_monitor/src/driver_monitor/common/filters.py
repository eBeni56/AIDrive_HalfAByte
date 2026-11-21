"""Frame-rate independent smoothing filters (all take timestamps in seconds)."""

from __future__ import annotations

import math


class Ema:
    """Exponential moving average with a time constant, so it behaves the same at 15 or 30 FPS."""

    def __init__(self, tau_s: float) -> None:
        self.tau_s = tau_s
        self.value: float | None = None
        self._t: float | None = None

    def reset(self) -> None:
        self.value, self._t = None, None

    def update(self, x: float, t: float) -> float:
        if self.value is None or self._t is None or self.tau_s <= 0:
            self.value, self._t = x, t
            return x
        dt = max(t - self._t, 1e-4)
        self._t = t
        alpha = 1.0 - math.exp(-dt / self.tau_s)
        self.value += alpha * (x - self.value)
        return self.value


class AsymmetricEma:
    """EMA that rises with one time constant and falls with another ("fast up, slow down")."""

    def __init__(self, rise_tau_s: float, fall_tau_s: float) -> None:
        self.rise_tau_s, self.fall_tau_s = rise_tau_s, fall_tau_s
        self.value: float | None = None
        self._t: float | None = None

    def reset(self) -> None:
        self.value, self._t = None, None

    def update(self, x: float, t: float) -> float:
        if self.value is None or self._t is None:
            self.value, self._t = x, t
            return x
        dt = max(t - self._t, 1e-4)
        self._t = t
        tau = self.rise_tau_s if x > self.value else self.fall_tau_s
        alpha = 1.0 - math.exp(-dt / max(tau, 1e-4))
        self.value += alpha * (x - self.value)
        return self.value


class OneEuroFilter:
    """One-Euro filter (Casiez et al., CHI 2012): smooths a lot when still, little when moving fast."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.05, d_cutoff: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x: float | None = None
        self._dx = 0.0
        self._t: float | None = None

    def reset(self) -> None:
        self._x, self._dx, self._t = None, 0.0, None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def update(self, x: float, t: float) -> float:
        if self._x is None or self._t is None:
            self._x, self._t = x, t
            return x
        dt = t - self._t
        if dt <= 0:
            return self._x
        self._t = t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx
        a = self._alpha(self.min_cutoff + self.beta * abs(self._dx), dt)
        self._x = a * x + (1.0 - a) * self._x
        return self._x


class Differentiator:
    """Smoothed time derivative of a signal (units per second)."""

    def __init__(self, tau_s: float) -> None:
        self._ema = Ema(tau_s)
        self._prev: tuple[float, float] | None = None

    def reset(self) -> None:
        self._ema.reset()
        self._prev = None

    @property
    def value(self) -> float:
        return self._ema.value or 0.0

    def update(self, x: float, t: float) -> float:
        if self._prev is None:
            self._prev = (t, x)
            return self._ema.update(0.0, t)
        t0, x0 = self._prev
        self._prev = (t, x)
        dt = t - t0
        if dt <= 0:
            return self.value
        return self._ema.update((x - x0) / dt, t)
