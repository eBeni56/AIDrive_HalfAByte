"""Overall drowsiness: evidence that ACCUMULATES over time -> score -> level that also RECOVERS.

Tiredness is a trend, not a single moment. Acute dangers (eyes closed > 1 s = MICROSLEEP, a falling head)
are alarmed instantly by their own detections; for the level they are only evidence:

evidence (0..1 each, 0.25 ~ slight, 0.5 ~ drowsy, 0.75 ~ very drowsy)
  - sliding 60 s eye statistics: HIGH_PERCLOS, HEAVY_EYELIDS, SLOW_BLINKS, FREQUENT_LONG_BLINKS
    (they fall away by themselves once the eyes behave normally again)
  - RECENT_EVENTS: the decaying fatigue load (FatigueLedger, half-life 5 min) of every eye closure
    >= 0.5 s (weighted by its duration), head falls, nods and yawns. Two patterns, both drowsiness:
      acute   one long closure: alarmed instantly (MICROSLEEP CRITICAL), weighs heavily, then fades
      chronic many shorter closures over minutes: each light, but they stack while they keep coming
    One 2 s closure ~ slightly drowsy for a few minutes; repeated events -> drowsy / very / severely
raw score = max(evidence) + others_weight * sum(other evidence)          (several cues add up)
score     = asymmetric EMA (rises in seconds, falls over ~15 s)
level     = thresholds on the score; it rises immediately and drops at most one step per
            level_drop_interval_s. Fatigue memory: only after reaching VERY_DROWSY the level never drops
            below (peak - memory_levels) in this session - one short event never leaves a memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from driver_monitor.common.enums import DrowsinessLevel
from driver_monitor.common.filters import AsymmetricEma
from driver_monitor.config import DrowsinessConfig
from driver_monitor.state.eye_stats import EyeStats
from driver_monitor.state.fatigue import FatigueSnapshot, curve

# event kind in the ledger -> name in `contributing`
_EVENT_NAMES = {
    "MICROSLEEP": "MICROSLEEPS",
    "HEAD_FALL": "HEAD_FALLING",
    "HEAD_NOD": "HEAD_NODS",
    "YAWN": "YAWNING",
    "EYES_CLOSED": "LONG_EYE_CLOSURES",
}
__all__ = ["DrowsinessEstimator", "DrowsinessInputs", "DrowsinessState", "curve"]


@dataclass(slots=True)
class DrowsinessInputs:
    eye: EyeStats
    fatigue: FatigueSnapshot


@dataclass(slots=True)
class DrowsinessState:
    level: DrowsinessLevel
    score: float
    raw_score: float
    peak_level: DrowsinessLevel
    previous_level: DrowsinessLevel
    level_since_t: float
    floor_level: DrowsinessLevel
    next_possible_drop_t: float | None
    contributing: list[str]
    evidence: dict[str, float] = field(default_factory=dict)
    warming_up: bool = True
    fatigue_load: float = 0.0


class DrowsinessEstimator:
    def __init__(self, cfg: DrowsinessConfig) -> None:
        self.cfg = cfg
        self._ema = AsymmetricEma(cfg.score_rise_tau_s, cfg.score_fall_tau_s)
        self._level = 0
        self._previous = 0
        self._peak = 0
        self._changed_t: float | None = None

    def reset(self) -> None:
        self._ema.reset()
        self._level = self._previous = self._peak = 0
        self._changed_t = None

    def evidence(self, x: DrowsinessInputs) -> dict[str, float]:
        c, e = self.cfg, x.eye
        ev: dict[str, float] = {}
        if e.perclos is not None:
            ev["HIGH_PERCLOS"] = curve(e.perclos, c.perclos_curve)
        if e.mean_openness is not None:
            ev["HEAVY_EYELIDS"] = curve(e.mean_openness, c.openness_curve)
        if e.mean_blink_duration_ms is not None and e.blink_count >= 3:
            ev["SLOW_BLINKS"] = curve(e.mean_blink_duration_ms, c.blink_duration_curve)
        ev["FREQUENT_LONG_BLINKS"] = curve(e.long_blinks, c.long_blink_curve)
        ev["RECENT_EVENTS"] = curve(x.fatigue.load, c.load_curve)
        return ev

    def update(self, x: DrowsinessInputs, t: float) -> DrowsinessState:
        c = self.cfg
        if self._changed_t is None:
            self._changed_t = t
        ev = self.evidence(x)
        values = sorted(ev.values(), reverse=True)
        top = values[0] if values else 0.0
        raw = min(1.0, top + c.others_weight * sum(values[1:]))
        score = self._ema.update(raw, t)

        from_score = sum(1 for thr in c.level_thresholds if score >= thr)
        floor = max(0, self._peak - c.memory_levels) if self._peak >= c.memory_min_peak else 0
        target = max(from_score, floor)

        next_drop: float | None = None
        if target > self._level:
            self._previous, self._level, self._changed_t = self._level, target, t
        elif target < self._level:
            next_drop = self._changed_t + c.level_drop_interval_s
            if t >= next_drop:
                self._previous, self._level, self._changed_t = self._level, self._level - 1, t
                next_drop = t + c.level_drop_interval_s if target < self._level else None
        self._peak = max(self._peak, self._level)

        contributing: list[str] = []
        for name, value in sorted(ev.items(), key=lambda kv: -kv[1]):
            if value < c.contributing_min:
                continue
            if name == "RECENT_EVENTS":  # name the events that make up the load
                kinds = sorted(x.fatigue.load_by_kind.items(), key=lambda kv: -kv[1])
                contributing += [_EVENT_NAMES.get(k, k) for k, v in kinds if v >= 0.2]
            else:
                contributing.append(name)
        return DrowsinessState(
            level=DrowsinessLevel.from_index(self._level),
            score=score,
            raw_score=raw,
            peak_level=DrowsinessLevel.from_index(self._peak),
            previous_level=DrowsinessLevel.from_index(self._previous),
            level_since_t=self._changed_t,
            floor_level=DrowsinessLevel.from_index(floor),
            next_possible_drop_t=next_drop,
            contributing=contributing,
            evidence=ev,
            warming_up=x.eye.warming_up,
            fatigue_load=x.fatigue.load,
        )
