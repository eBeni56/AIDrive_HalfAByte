"""COACH: decides WHEN the LLM (in safety_ai) is asked for a driving tip or a debrief. The text it gets comes
from runlog.py: facts only - finding the pattern, the advice and the words is the LLM's job.

A live tip is asked for when ALL of these hold:
  evidence   since the last tip: >= MIN_SAME warnings of the same category (left side, right side, ahead,
             behind, eyes closed, phone, looking away), or >= MIN_TOTAL warnings in total (one mistake is
             noise, a repeat is a pattern); or a good streak: STREAK_N road users noticed in time in a row
             and no warning for STREAK_QUIET_S (praise). Never on a timer alone: without new evidence the
             LLM could only produce generic advice.
  cooldown   >= COOLDOWN_S since the last tip (more often is nagging).
  calm       right now: no driver problem active, no road user's look window open, the last warning
             >= CALM_AFTER_WARNING_S ago (it has been spoken). A tip never talks over a hazard. If no calm
             moment comes within CALM_WAIT_S the tip is dropped; the debrief still covers it.
A debrief is asked for at EVERY simulation_ended, however short the run and even if nothing happened (then the
LLM says the drive was good).

coach_request: {"type": "coach_request", "kind": "tip" | "debrief", "id", "t_s", "report": "<text>"}
"""
import itertools
import logging
from collections import Counter

MIN_SAME = 2
MIN_TOTAL = 3
COOLDOWN_S = 40.0
CALM_AFTER_WARNING_S = 3.0
CALM_WAIT_S = 20.0
STREAK_N = 6
STREAK_QUIET_S = 45.0

log = logging.getLogger("coach")


class Coach:
    def __init__(self):
        self._ids = itertools.count(1)
        self.start(None)

    def start(self, now):
        """simulation_started: a new run, nothing collected yet."""
        self.categories = []        # categories of the (first, not repeated) warnings since the last tip
        self.streak = 0             # road users noticed in time since the last warning
        self.last_warning_at = None
        self.last_tip_at = None
        self.last_tip_t_s = None    # run time of the last tip: the next report starts after it
        self.waiting_since = None   # evidence is there, waiting for a calm moment since

    # ---- input ----

    def on_warning(self, w, now):
        self.last_warning_at = now
        if not w.get("repeat"):
            self.categories.append(w.get("category"))
            self.streak = 0

    def on_attention(self, ev):
        if ev.get("event") == "attention_event" and ev.get("kind") == "noticed":
            self.streak += 1

    # ---- decisions ----

    def _evidence(self, now):
        if self.categories:
            if max(Counter(self.categories).values()) >= MIN_SAME or len(self.categories) >= MIN_TOTAL:
                return "pattern"
        quiet_for = None if self.last_warning_at is None else now - self.last_warning_at
        if self.streak >= STREAK_N and (quiet_for is None or quiet_for >= STREAK_QUIET_S):
            return "streak"
        return None

    def update(self, now, calm, run):
        """~20 times per second. Returns a coach_request (dict) or None. `run`: the RunLog."""
        if not run.running:
            return None
        evidence = self._evidence(now)
        if evidence is None:
            self.waiting_since = None
            return None
        if self.last_tip_at is not None and now - self.last_tip_at < COOLDOWN_S:
            return None                                    # keep collecting, ask after the cooldown
        if self.waiting_since is None:
            self.waiting_since = now
        quiet = self.last_warning_at is None or now - self.last_warning_at >= CALM_AFTER_WARNING_S
        if not (calm and quiet):
            if now - self.waiting_since > CALM_WAIT_S:
                log.info("no calm moment for %.0f s: tip dropped (%s)", CALM_WAIT_S, evidence)
                self._used()
            return None
        t_s = run.t_s(now)
        req = {"type": "coach_request", "kind": "tip", "id": f"tip-{next(self._ids)}", "t_s": t_s,
               "report": run.tip_report(now, self.last_tip_t_s)}
        self._used()
        self.last_tip_at, self.last_tip_t_s = now, t_s
        return req

    def _used(self):
        """The collected evidence was used (or dropped): start collecting again."""
        self.categories, self.streak, self.waiting_since = [], 0, None

    def debrief(self, now, info):
        """simulation_ended. `info`: RunLog.end(). Returns a coach_request (dict) or None."""
        if not info["report"]:   # no run was going on (an end without a start): nothing to analyse
            log.info("no debrief: no run")
            return None
        return {"type": "coach_request", "kind": "debrief", "id": f"debrief-{next(self._ids)}",
                "t_s": info["duration_s"], "report": info["report"]}
