"""The LLM coach: turns central_point's run reports into a spoken tip or a debrief.

Azure OpenAI (Microsoft Foundry), Responses API, deployment AZURE_OPENAI_DEPLOYMENT (gpt-4.1-mini), settings in
safety_ai/.env (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT; never in git). Standard library
only: one kept-alive HTTPS connection, answers forced into a strict JSON schema.

  tip(report)     -> {"speak": bool, "focus": str, "say": str}                    or None (no answer)
  debrief(report) -> {"verdict": "dangerous" | "needs work" | "good" | "very good", "say": str (spoken, ~100
                     words), "mistakes": [{"problem", "evidence", "fix"}], "went_well": [str], "next_focus": str}
                     or None: the end-of-drive analysis from the full statistics of the run

The calls block (tip ~1 s, debrief ~2-4 s); safety_ai runs them in a worker thread.
"""
import http.client
import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ENV_FILE = Path(__file__).resolve().parent / ".env"
TIMEOUT_S = 20.0
RESPONSES_PATH = "/openai/v1/responses"   # resource-level route: the project URL refuses api-key auth

log = logging.getLogger("coach_llm")

TIP_SYSTEM = """You are the calm, friendly voice of a car's driving coach. The car's cameras logged what the driver did. You get the recent events, the totals of this drive and the tips you already gave in this drive.

Give ONE short spoken tip about the most important thing in the recent events: say what happened, then exactly what to do differently (where to look, how often, what to stop doing). If the driver did well, praise the specific thing they did well instead.

Rules:
- The tip is about the events listed under "Since your last tip" (or "So far in this drive"). The totals and the earlier tips are background only: never bring up a problem that is not in those recent events.
- Use only facts from the log. Never invent events, numbers, places or reasons, and never guess a cause ("because ...") the log does not show.
- Connect two events only if their times are a few seconds apart (for example a miss right after a phone use or a look down).
- If the problem of an earlier tip has not come back since that tip, first say in a few words that it got better.
- Do not repeat an earlier tip unless that problem is still the biggest one; then say it more firmly.
- A pattern means the same kind of problem at least twice (same side, eyes closed again, phone again). If the recent events are only unrelated one-offs, set "speak" to false.
- Closed eyes more than once are a sign of tiredness: suggest a break.
- At most 20 words, one or two short sentences, plain spoken English, talk to the driver as "you". Write numbers and units the way they are spoken ("two cars", "1.5 seconds", "70 percent"); no lists, emojis or abbreviations.
- "focus" names the topic in one or two words (for example "right side", "mirrors", "phone", "tiredness", "praise")."""

DEBRIEF_SYSTEM = """You are a calm, experienced driving instructor. A simulated drive just ended. The car's cameras measured everything the driver did; you get the full statistics of the drive, the warnings the car spoke, the tips given during the drive, and short summaries of the driver's previous drives.

Write the end-of-drive analysis that will be read aloud to the driver, plus a short version for the screen.

How to analyse:
1. Rank the problems by danger: eyes closed > a missed pedestrian or a missed road user ahead > phone use > missed road users beside or behind > looking down or up > slow reactions > weak mirror habits.
2. For each problem use the numbers from the log (how many, how long, where). Point out a link between two things only when the log shows it: "gaze during the look window" of a missed road user, or two events a few seconds apart. Never guess other causes.
3. For each problem give a concrete fix the driver can do next time: where to look, how often, what to stop doing (for example: a mirror check every five to eight seconds; a glance at the mirror before anything can reach the blind spot; the phone out of reach before driving; a break when the eyes close more than once).
4. Name one or two things that really went well, with numbers. If the drive was good, say so plainly and do not invent problems.
5. If there are previous drives, say clearly what improved and what got worse, with the numbers before and now.
6. If "no camera data" is above 15 percent: say early that the camera lost the driver's face for that share of the drive, so this analysis is less reliable; a road user missed while the gaze shows "no camera data" may not be the driver's fault, so do not count it as a mistake and do not judge the mirror habits.
7. Eyes closed two or more times means the driver is tired: the most important advice is to stop and rest before driving again. Never answer tiredness with "keep your eyes open".
8. Compare with previous drives only on what their summaries contain (noticed in time, average reaction, eyes closed, phone, looked up or down). Never claim a change in anything else.
9. "evidence" copies the numbers of that same problem exactly from the log; never mix numbers of different events or turn a rate into a duration.

The spoken text "say": 70 to 110 words (a hard limit), plain spoken English, talk to the driver as "you". Start with a one-sentence overall verdict. Cover at most the three biggest problems. End with the one thing to focus on in the next drive. Write numbers and units the way they are spoken ("two cars", "1.5 seconds", "70 percent"); no lists, no emojis, no abbreviations, no times like "1:42" (say "near the end" or "after about two minutes" instead).

Use only facts from the log. Never invent events, numbers or places."""


def _schema(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


TIP_SCHEMA = _schema(speak={"type": "boolean"}, focus={"type": "string"}, say={"type": "string"})
DEBRIEF_SCHEMA = _schema(
    verdict={"type": "string", "enum": ["dangerous", "needs work", "good", "very good"]},
    say={"type": "string"},
    mistakes={"type": "array", "items": _schema(problem={"type": "string"}, evidence={"type": "string"},
                                                fix={"type": "string"})},
    went_well={"type": "array", "items": {"type": "string"}},
    next_focus={"type": "string"})


def load_env(path=ENV_FILE):
    """KEY=value lines of safety_ai/.env (comments and blank lines skipped)."""
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def clean(text, max_words):
    """One line of plain text, cut at the last sentence end within max_words (the schema can't count)."""
    text = re.sub(r"[*_#`\"]", "", re.sub(r"\s+", " ", text or "")).strip()
    words = text.split()
    if len(words) <= max_words:
        return text
    cut = " ".join(words[:max_words])
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    return cut[:end + 1] if end > 0 else cut + "."


class CoachLLM:
    def __init__(self, env):
        missing = [k for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT") if not env.get(k)]
        if missing:
            raise ValueError(f"missing in {ENV_FILE}: {', '.join(missing)}")
        self.host = urlsplit(env["AZURE_OPENAI_ENDPOINT"]).netloc
        self.key = env["AZURE_OPENAI_KEY"]
        self.deployment = env["AZURE_OPENAI_DEPLOYMENT"]
        self.conn = None
        self.lock = threading.Lock()   # one request at a time on the kept-alive connection

    def tip(self, report):
        ans = self._ask(TIP_SYSTEM, report, TIP_SCHEMA, "tip", 120, 0.5)
        if ans is None:
            return None
        ans["say"] = clean(ans.get("say"), 30)
        ans["speak"] = bool(ans.get("speak")) and bool(ans["say"])
        return ans

    def debrief(self, report):
        ans = self._ask(DEBRIEF_SYSTEM, report, DEBRIEF_SCHEMA, "debrief", 900, 0.4)
        if ans is None:
            return None
        ans["say"] = clean(ans.get("say"), 130)
        ans["next_focus"] = clean(ans.get("next_focus"), 40)
        return ans if ans["say"] else None

    def warm_up(self):
        """Open the TLS connection now so the first real request is not slower."""
        return self._ask("Answer with one word.", "Say OK.", None, "ping", 16, 0) is not None

    # ---- HTTP ----

    def _ask(self, system, user, schema, name, max_tokens, temperature):
        body = {"model": self.deployment, "max_output_tokens": max_tokens, "temperature": temperature,
                "input": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if schema:
            body["text"] = {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}}
        t0 = time.perf_counter()
        try:
            status, text = self._post(json.dumps(body))
        except (OSError, http.client.HTTPException) as e:
            log.warning("%s: no answer from Azure (%s)", name, e)
            return None
        ms = (time.perf_counter() - t0) * 1000
        if status != 200:
            log.warning("%s: Azure answered HTTP %d: %s", name, status, text[:300])
            return None
        try:
            data = json.loads(text)
            out = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                          for c in item.get("content", []) if c.get("type") == "output_text")
            ans = json.loads(out) if schema else {"text": out}
        except (ValueError, AttributeError) as e:
            log.warning("%s: unreadable answer (%s)", name, e)
            return None
        log.info("%s answered in %.0f ms", name, ms)
        return ans

    def _post(self, body):
        headers = {"api-key": self.key, "Content-Type": "application/json"}
        with self.lock:
            for attempt in range(2):          # a kept-alive connection may have been closed by the server
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(self.host, timeout=TIMEOUT_S)
                try:
                    self.conn.request("POST", RESPONSES_PATH, body, headers)
                    r = self.conn.getresponse()
                    return r.status, r.read().decode("utf-8", "replace")
                except (OSError, http.client.HTTPException):
                    self.conn.close()
                    self.conn = None
                    if attempt:
                        raise
        raise RuntimeError("unreachable")
