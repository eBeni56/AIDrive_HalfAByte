"""safety_ai: the car's voice and its LLM driving coach. Runs on this PC (it needs the speakers).

Connects to central_point as {"type": "hello", "role": "safety_ai"} and gets:
  warning         -> spoken at once (fixed line from central_point; more urgent interrupts less urgent)
  coach_request   -> the LLM (coach_llm.py, Azure) writes a tip or a debrief from the report; it is spoken and
                     sent back as coach_result, which central_point shows on the dashboard and remembers.
A tip is not spoken (and not sent back) when a warning came in while the LLM was writing it: a tip must never
compete with a hazard. Reconnects every RETRY_S when central_point is not running.

  python safety_ai/main.py [--hub ws://localhost:8765] [--voice female_1] [--mute] [--no-llm]
"""
import argparse
import asyncio
import json
import logging
import os
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

import coach_llm
from voice import VOICES, Voice

RETRY_S = 2.0
WARNING_MAX_AGE_S = 2.5    # a warning that could not be spoken within this is dropped
COACH_MAX_AGE_S = 20.0     # a tip / debrief waits for the speaker at most this long
COACH_PRIORITY = 0         # below every warning (1..3)

log = logging.getLogger("safety_ai")


class SafetyAI:
    def __init__(self, voice, llm):
        self.voice = voice
        self.llm = llm
        self.last_warning_at = 0.0
        self.tasks = set()        # running coach calls (asyncio keeps only weak references)

    async def run(self, url):
        while True:
            try:
                async with connect(url, open_timeout=5) as ws:
                    await ws.send(json.dumps({"type": "hello", "role": "safety_ai"}))
                    log.info("connected to central_point at %s", url)
                    async for raw in ws:
                        self.handle(ws, raw)
                log.info("central_point closed the connection")
            except (OSError, TimeoutError, WebSocketException) as e:
                # not running yet, restarting, or Docker's port proxy answering before the server is up
                # (a half-open connection: InvalidMessage): all mean "try again"
                log.info("central_point not reachable at %s (%s); retrying every %g s", url, e, RETRY_S)
            except Exception:   # never let the voice die: log it and reconnect
                log.exception("unexpected error in the connection to central_point; reconnecting")
            await asyncio.sleep(RETRY_S)

    def handle(self, ws, raw):
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "warning" and isinstance(msg.get("say"), str):
            self.last_warning_at = time.monotonic()
            self.voice.drop_below(COACH_PRIORITY + 1)                # waiting tips are out of date now
            self.voice.say(msg["say"], int(msg.get("priority", 1)), WARNING_MAX_AGE_S)
        elif msg.get("type") == "coach_request" and msg.get("kind") in ("tip", "debrief"):
            task = asyncio.create_task(self.coach(ws, msg))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def coach(self, ws, req):
        kind, req_id = req["kind"], req.get("id")
        if self.llm is None:
            log.info("%s %s ignored: no LLM (--no-llm or no key in safety_ai/.env)", kind, req_id)
            return
        asked_at = time.monotonic()
        ask = self.llm.tip if kind == "tip" else self.llm.debrief
        ans = await asyncio.to_thread(ask, str(req.get("report", "")))
        if ans is None:
            log.warning("%s %s: no answer from the LLM, nothing said", kind, req_id)
            return
        if kind == "tip" and not ans["speak"]:
            log.info("tip %s: the LLM found nothing worth saying", req_id)
        elif kind == "tip" and self.last_warning_at > asked_at:
            log.info("tip %s dropped: a warning came in meanwhile (%s)", req_id, ans["say"])
            return
        else:
            self.voice.say(ans["say"], COACH_PRIORITY, COACH_MAX_AGE_S)
        try:
            await ws.send(json.dumps({"type": "coach_result", "request_id": req_id, "kind": kind, **ans}))
        except ConnectionClosed:
            log.info("%s %s: central_point is gone, the result was not sent back", kind, req_id)


def make_llm(no_llm):
    if no_llm:
        log.info("--no-llm: warnings only, no tips or debriefs")
        return None
    try:
        return coach_llm.CoachLLM(coach_llm.load_env())
    except ValueError as e:
        log.error("no LLM coach (%s): warnings only", e)
        return None


async def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--hub", default=os.environ.get("SAFETY_AI_HUB", "ws://localhost:8765"),
                   help="central_point address (env SAFETY_AI_HUB)")
    p.add_argument("--voice", default=os.environ.get("SAFETY_AI_VOICE", "female_1"),
                   help=f"one of {', '.join(VOICES)} or a Phonon voice name (env SAFETY_AI_VOICE)")
    p.add_argument("--mute", action="store_true", help="print the lines instead of speaking them")
    p.add_argument("--no-llm", action="store_true", help="warnings only: no tips or debriefs")
    args = p.parse_args()

    voice = Voice(args.voice, mute=args.mute)
    llm = make_llm(args.no_llm)
    if llm is not None:   # open the HTTPS connection now, so the first tip is not slower
        ok = await asyncio.to_thread(llm.warm_up)
        log.info("LLM coach %s (%s)", "ready" if ok else "NOT reachable now (will retry per request)", llm.deployment)
    await SafetyAI(voice, llm).run(args.hub)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
