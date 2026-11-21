"""
Co-Pilot Voice & Text Client (Barge-In + Echo Guard + Proactive S1 Push Receiver)
=================================================================================
- Registers as a `voice_client` on copilot/ai-server.py (ws://localhost:8766) so automatic
  S1 safety alerts and proactive S2 check-ins speak aloud IMMEDIATELY even when IDLE.
- Driver can barge in at any time ("Hey Jarvis ...", "Stop", "Stop talking", "Be quiet").
- On Stop: immediately cuts TTS, clears pending server reply, drains queued speech, closes window.
- Never triggers on Jarvis's own voice or short 1-word noise blips.

Usage:
  python -u copilot/voice_client.py --mic --voice female_1
  python -u copilot/voice_client.py --text --voice female_1
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import json
from pathlib import Path
import queue
import re
import sys
import threading
import time
import traceback
from typing import Any

from websockets.asyncio.client import connect

COPILOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = COPILOT_DIR.parent
STT_DIR = REPO_ROOT / "speech-recognition"
TTS_DIR = REPO_ROOT / "text-to-speech"

for _p in (STT_DIR, TTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DEFAULT_WS_URL = "ws://localhost:8766"
DEFAULT_PROFILE_PATH = COPILOT_DIR / "driver_profile.json"
DEFAULT_SESSION_ID = "driver-main"  # Shared with event_simulator.py for S1 -> S2 handoff

SILENCE_CONCAT_S = 0.75
ECHO_TAIL_WINDOW_S = 1.6
MAX_AUDIO_QUEUE_CHUNKS = 10

STOP_PHRASES = {
    "/stop", "stop", "stop talking", "stop listening", "jarvis stop",
    "cancel", "cancel that", "never mind", "nevermind", "be quiet",
    "quiet", "shut up", "dismiss", "enough",
}
BARGE_IN_STOP_SUBSTRINGS = (
    "stop talking", "stop listening", "jarvis stop", "be quiet", "shut up", "cancel that",
)
BARGE_IN_STOP_EXACT = {"stop", "stop it", "quiet", "cancel", "enough"}

RESET_PHRASES = {
    "/reset", "reset", "reset memory", "clear memory", "forget everything", "new conversation",
}
REPEAT_PHRASES = {
    "/repeat", "repeat", "repeat that", "say that again", "what did you say", "pardon",
}
THANKS_PHRASES = {"thanks", "thank you", "thanks jarvis", "thank you jarvis"}
IGNORE_BACKCHANNELS = {
    "yeah", "yes", "yep", "nope", "no", "ok", "okay", "right", "alright",
    "sure", "cool", "nice", "good", "fine", "oh", "and so", "so", "well", "bye",
    "ignored", "hello", "hi", "hmm",
}
ALLOWED_SINGLE_WORD_QUERIES = {
    "weather", "news", "time", "speed", "battery", "tired", "sleepy", "sleep", "help", "joke",
}


# =============================================================================
# ECHO & BARGE-IN MATCHERS
# =============================================================================
def _norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def extract_wake_word(text: str, wake_words: list[str]) -> tuple[bool, str]:
    for ww in sorted(wake_words, key=len, reverse=True):
        words = [re.escape(w) for w in ww.strip().lower().split() if w]
        if not words:
            continue
        pattern = r"\b" + r"[\s,.:;!?-]+".join(words) + r"\b[\s,.:;!?-]*"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return True, text[match.end() :].strip()
    return False, text.strip()


def detect_barge_in(heard: str, wake_words: list[str], spoken_now: str) -> str | None:
    """Returns 'stop', 'wake', or None if `heard` is just Jarvis's own voice."""
    heard_norm = " ".join(_norm_words(heard))
    spoken_norm = " ".join(_norm_words(spoken_now))
    if not heard_norm:
        return None

    for phrase in BARGE_IN_STOP_SUBSTRINGS:
        if re.search(rf"\b{re.escape(phrase)}\b", heard_norm) and phrase not in spoken_norm:
            return "stop"

    if heard_norm in BARGE_IN_STOP_EXACT and not re.search(rf"\b{re.escape(heard_norm)}\b", spoken_norm):
        return "stop"

    for ww in sorted(wake_words, key=len, reverse=True):
        ww_norm = " ".join(_norm_words(ww))
        if not ww_norm:
            continue
        if re.search(rf"\b{re.escape(ww_norm)}\b", heard_norm) and not re.search(
            rf"\b{re.escape(ww_norm)}\b", spoken_norm
        ):
            after = heard_norm.split(ww_norm, 1)[1].strip()
            if after in BARGE_IN_STOP_EXACT or any(p in after for p in BARGE_IN_STOP_SUBSTRINGS):
                return "stop"
            return "wake"

    return None


def is_trailing_echo(heard: str, last_spoken: str, wake_words: list[str]) -> bool:
    """Detects lingering acoustic echo right after Jarvis finishes speaking."""
    if not last_spoken:
        return False
    woke, _ = extract_wake_word(heard, wake_words)
    if woke and not extract_wake_word(last_spoken, wake_words)[0]:
        return False

    h_words = _norm_words(heard)
    s_words = _norm_words(last_spoken)
    if not h_words or not s_words:
        return False

    if len(h_words) >= 3:
        spoken_joined = " " + " ".join(s_words) + " "
        for i in range(len(h_words) - 2):
            if (" " + " ".join(h_words[i : i + 3]) + " ") in spoken_joined:
                return True

    s_set = set(s_words)
    overlap = sum(1 for w in h_words if w in s_set) / len(h_words)
    return overlap >= 0.60


# =============================================================================
# NON-BLOCKING, INTERRUPTIBLE TTS PLAYER
# =============================================================================
class TTSPlayer:
    def __init__(self, enabled: bool = True, voice: str = "female_1") -> None:
        self.enabled = enabled
        self.speaker = None
        self.is_speaking = threading.Event()
        self.current_text: str = ""
        self.last_text: str = ""
        self.last_finished_t: float = 0.0

        self._play_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._abort = threading.Event()
        self._gen = 0

        if not enabled:
            print("[TTS] Audio playback disabled (--no-tts).", flush=True)
            return
        try:
            from tts_driving import Speaker
            print(f"[INIT] Loading Phonon 31M TTS (voice={voice})...", flush=True)
            self.speaker = Speaker(voice=voice)
            print(f"[TTS] Ready (voice={self.speaker.voice}).", flush=True)
        except Exception as exc:
            print(
                f"[TTS WARNING] Could not load Phonon 31M TTS ({exc}). Falling back to print-only.\n"
                "Install with: python -m pip install -r text-to-speech/requirements.txt",
                file=sys.stderr,
                flush=True,
            )
            self.enabled = False

    def set_voice(self, voice: str) -> str:
        if self.speaker is not None:
            self.speaker.set_voice(voice)
            return self.speaker.voice
        return voice

    def interrupt(self) -> None:
        with self._state_lock:
            self._gen += 1
            self._abort.set()
            if self.current_text:
                self.last_text = self.current_text
            self.last_finished_t = time.monotonic()

    def start_speaking(
        self,
        text: str,
        priority: str = "normal",
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        clean = text.strip()
        tag = "URGENT TTS" if priority == "high" else "TTS"
        print(f"\n[{tag}] {clean}\n", flush=True)

        if not self.enabled or self.speaker is None or not clean:
            if on_done:
                on_done(False)
            return

        with self._state_lock:
            self._gen += 1
            my_gen = self._gen
            self._abort.set()

        def _worker() -> None:
            import numpy as np
            import sounddevice as sd

            interrupted = False
            with self._play_lock:
                with self._state_lock:
                    if my_gen != self._gen:
                        return
                    self._abort.clear()
                    self.current_text = clean
                    self.is_speaking.set()
                try:
                    sr = self.speaker.tts.sample_rate
                    step = max(1, int(sr * 0.04))  # 40 ms sub-chunks for clean instant stop
                    with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as out:
                        with self.speaker.tts.stream(clean, voice=self.speaker.voice) as audio:
                            for chunk in audio:
                                if self._abort.is_set() or my_gen != self._gen:
                                    interrupted = True
                                    break
                                pcm = np.asarray(chunk, dtype=np.float32).reshape(-1, 1)
                                for pos in range(0, len(pcm), step):
                                    if self._abort.is_set() or my_gen != self._gen:
                                        interrupted = True
                                        break
                                    out.write(pcm[pos : pos + step])
                                if interrupted:
                                    break
                except Exception as exc:
                    if not self._abort.is_set():
                        print(f"[TTS ERROR] Playback failed: {exc}", file=sys.stderr, flush=True)
                finally:
                    with self._state_lock:
                        self.last_text = clean
                        self.current_text = ""
                        self.last_finished_t = time.monotonic()
                        self.is_speaking.clear()
                    if on_done:
                        on_done(interrupted)

        threading.Thread(target=_worker, name=f"tts-{my_gen}", daemon=True).start()


def load_profile(path: Path) -> dict[str, Any]:
    if path.exists():
        print(f"[PROFILE] Loaded {path.name}", flush=True)
        return json.loads(path.read_text(encoding="utf-8"))
    print(f"[PROFILE] {path.name} not found, using built-in defaults.", flush=True)
    return {
        "driver_name": "Driver",
        "default_city": "Cluj-Napoca",
        "hobbies": ["Formula 1", "sim racing", "automotive tech", "football"],
        "wake_words": ["hey jarvis", "hi jarvis", "ok jarvis", "jarvis"],
        "listen_window_s": 15.0,
    }


class SpeechAggregator:
    """Concatenates consecutive user STT lines until `silence_s` passes."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        out_queue: asyncio.Queue[str],
        silence_s: float = SILENCE_CONCAT_S,
    ) -> None:
        self.loop = loop
        self.out_queue = out_queue
        self.silence_s = silence_s
        self._parts: list[str] = []
        self._timer: asyncio.TimerHandle | None = None

    def on_partial_threadsafe(self) -> None:
        self.loop.call_soon_threadsafe(self._on_partial)

    def on_completed_threadsafe(self, text: str) -> None:
        self.loop.call_soon_threadsafe(self._on_completed, text)

    def reset_and_drain_threadsafe(self) -> None:
        self.loop.call_soon_threadsafe(self._reset_and_drain)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _reset_and_drain(self) -> None:
        self._cancel_timer()
        self._parts.clear()
        while True:
            try:
                self.out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _on_partial(self) -> None:
        if self._parts:
            self._cancel_timer()

    def _on_completed(self, text: str) -> None:
        self._parts.append(text.strip())
        self._cancel_timer()
        self._timer = self.loop.call_later(self.silence_s, self._flush)

    def _flush(self) -> None:
        self._timer = None
        if not self._parts:
            return
        merged = re.sub(r"\s+", " ", " ".join(self._parts)).strip()
        self._parts.clear()
        if merged:
            print(f"[UTTERANCE] {merged}", flush=True)
            self.out_queue.put_nowait(merged)


class CoPilotClient:
    def __init__(
        self,
        ws_url: str,
        profile: dict[str, Any],
        tts: TTSPlayer,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> None:
        self.ws_url = ws_url
        self.profile = profile
        self.tts = tts
        self.session_id = session_id
        self.wake_words: list[str] = [str(w).lower() for w in profile.get("wake_words", ["hey jarvis"])]
        self.listen_window_s = float(profile.get("listen_window_s", 15.0))
        self.default_city = str(profile.get("default_city", "Cluj-Napoca"))
        self.active_until = 0.0
        self.last_spoken: str | None = None
        self._req_counter = 0

        # Tracks the ONE active user request; set to None on Stop/Barge-In so stale replies are discarded
        self.pending_req_id: str | None = None
        self.waiting_for_server = threading.Event()

        self.vehicle: dict[str, Any] = {"speed_kmh": 95, "battery_pct": 62}
        self.driver: dict[str, Any] = {"face_detected": True, "drowsiness_level": "ALERT", "eyes_state": "OPEN"}

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"req-{self._req_counter}"

    @property
    def is_listening(self) -> bool:
        return time.monotonic() < self.active_until

    @property
    def is_busy_output(self) -> bool:
        return self.tts.is_speaking.is_set() or self.waiting_for_server.is_set()

    def open_window(self) -> None:
        self.active_until = time.monotonic() + self.listen_window_s

    def close_window(self) -> None:
        self.active_until = 0.0

    def cancel_active_turn(self, close_window: bool = False) -> None:
        """Immediately halts TTS and invalidates any in-flight server reply."""
        self.pending_req_id = None
        self.waiting_for_server.clear()
        self.tts.interrupt()
        if close_window:
            self.close_window()

    def _say(self, text: str, priority: str = "normal", keep_open_after: bool = True) -> None:
        self.last_spoken = text
        if keep_open_after:
            self.open_window()

        def _after(interrupted: bool) -> None:
            if keep_open_after and not interrupted:
                self.open_window()

        self.tts.start_speaking(text, priority=priority, on_done=_after)

    def _base_payload(self, driver_message: str) -> dict[str, Any]:
        req_id = self._next_id()
        return {
            "request_id": req_id,
            "session_id": self.session_id,
            "mode": "info",
            "allow_tools": True,
            "profile": {
                "default_city": self.default_city,
                "hobbies": self.profile.get("hobbies", []),
            },
            "driver": dict(self.driver),
            "vehicle": dict(self.vehicle),
            "driver_message": driver_message,
        }

    async def _send_async(self, ws, payload: dict[str, Any]) -> None:
        self.pending_req_id = payload.get("request_id")
        self.waiting_for_server.set()
        await ws.send(json.dumps(payload, ensure_ascii=False))

    def handle_server_message(self, msg: dict[str, Any]) -> None:
        """Processes EVERY incoming message from ai-server.py (both user replies and pushed S1 alerts)."""
        ev = msg.get("event")
        if ev == "registered":
            print("[WS] Subscribed to automatic S1 / proactive server alerts!", flush=True)
            return
        if ev == "copilot_error":
            self.waiting_for_server.clear()
            self.pending_req_id = None
            print(f"[SERVER ERROR] {msg.get('error')}", flush=True)
            return
        if ev != "copilot_response":
            return

        # 1. Server-pushed automatic alert (from event_simulator.py or camera/sim server)
        if msg.get("pushed"):
            mode = str(msg.get("mode") or "urgent").upper()
            priority = str(msg.get("priority") or ("high" if mode == "URGENT" else "normal"))
            preset = msg.get("preset_name") or "Live Event"
            latency = msg.get("latency_ms", 0.0)
            print(f"\n[AUTO EVENT -> {mode} | {preset} | {latency}ms]", flush=True)
            if priority == "high":
                # S1 urgent preempts any pending INFO request and any ongoing INFO speech immediately
                self.pending_req_id = None
                self.waiting_for_server.clear()
            if msg.get("spoken_text"):
                # Speak immediately even when IDLE, then open 15s window so driver can talk back to S2
                self._say(msg["spoken_text"], priority=priority, keep_open_after=True)
            return

        # 2. Reply to our own voice/text request -> drop silently if cancelled or superseded
        req_id = msg.get("request_id")
        if req_id != self.pending_req_id:
            print(f"[CANCELLED - dropped stale reply {req_id}]", flush=True)
            return

        self.pending_req_id = None
        self.waiting_for_server.clear()

        if msg.get("tool_used"):
            print(f"[SERVER TOOL] {msg['tool_used']}", flush=True)

        action = msg.get("action")
        if action == "stop":
            self.cancel_active_turn(close_window=True)
            if msg.get("spoken_text"):
                self._say(msg["spoken_text"], keep_open_after=False)
            return

        if msg.get("spoken_text"):
            self._say(msg["spoken_text"], priority=msg.get("priority", "normal"), keep_open_after=True)

    async def handle_utterance(self, ws, raw_text: str) -> None:
        text = raw_text.strip()
        if not text:
            return

        if text.lower().startswith("/voice "):
            chosen = self.tts.set_voice(text.split(maxsplit=1)[1].strip())
            self._say(f"Voice switched to {chosen}.")
            return

        if text.startswith("!"):
            payload = {
                "request_id": self._next_id(),
                "session_id": self.session_id,
                "mode": "urgent",
                "situation": text[1:].strip(),
                "goal": "Give the immediate safe driving reflex.",
            }
            await self._send_async(ws, payload)
            return

        low = text.lower().strip(" .!?,")
        if low.startswith("/news") or low.startswith("/weather"):
            parts = text.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1].strip() if len(parts) > 1 else ""
            payload = self._base_payload(f"Run {cmd} {args}".strip())
            payload["force_command"] = cmd
            payload["force_args"] = args
            await self._send_async(ws, payload)
            return

        if low in RESET_PHRASES:
            self.cancel_active_turn(close_window=False)
            await self._send_async(ws, {
                "event": "reset_session",
                "request_id": self._next_id(),
                "session_id": self.session_id,
            })
            return

        if low in REPEAT_PHRASES and self.is_listening:
            self._say(self.last_spoken or "I have not said anything yet.")
            return

        if low in STOP_PHRASES:
            self.cancel_active_turn(close_window=True)
            print("[STOP] Standing by.", flush=True)
            return

        # Wake-word gate + 15s active window
        woke, remainder = extract_wake_word(text, self.wake_words)
        if woke:
            rem_clean = re.sub(r"[^a-z0-9/\s]", "", remainder.lower()).strip()
            if not rem_clean or rem_clean in IGNORE_BACKCHANNELS:
                print(f"[WAKE] Jarvis listening for {self.listen_window_s:.0f}s...", flush=True)
                self._say("Yes, I am listening.")
                return
            self.open_window()
            text = remainder
        elif not self.is_listening:
            print(f"[IDLE - ignored] {text}", flush=True)
            return

        low_cmd = re.sub(r"[^a-z0-9/\s]", "", text.lower()).strip()
        words = low_cmd.split()

        if low_cmd in STOP_PHRASES or re.search(r"\b(stop talking|stop listening|stop stop|cancel|be quiet|shut up)\b", low_cmd):
            self.cancel_active_turn(close_window=True)
            print("[STOP] Standing by.", flush=True)
            return
        if low_cmd in RESET_PHRASES:
            self.cancel_active_turn(close_window=False)
            await self._send_async(ws, {
                "event": "reset_session",
                "request_id": self._next_id(),
                "session_id": self.session_id,
            })
            return
        if low_cmd in REPEAT_PHRASES:
            self._say(self.last_spoken or "I have not said anything yet.")
            return
        if low_cmd in THANKS_PHRASES:
            self.close_window()
            self._say("You are welcome.", keep_open_after=False)
            return
        # Drop 1-word noise/backchannels (e.g. "Ignored.", "Yeah.", "Okay.") so Jarvis doesn't ramble
        if low_cmd in IGNORE_BACKCHANNELS or (len(words) == 1 and words[0] not in ALLOWED_SINGLE_WORD_QUERIES):
            print(f"[NOISE/BACKCHANNEL - skipped] {text}", flush=True)
            return

        await self._send_async(ws, self._base_payload(text))


# =============================================================================
# SHARED ASYNC WS RECEIVER + RUNNERS
# =============================================================================
async def _recv_loop(ws, client: CoPilotClient) -> None:
    async for raw in ws:
        try:
            msg = json.loads(raw)
            if isinstance(msg, dict):
                client.handle_server_message(msg)
        except Exception as exc:
            print(f"[WS PARSE ERROR] {exc}", file=sys.stderr, flush=True)


def list_devices() -> None:
    import sounddevice as sd
    devices = sd.query_devices()
    print("Available audio input devices:", flush=True)
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            mark = " (default)" if idx == sd.default.device[0] else ""
            print(f"  [{idx}] {dev['name']} ({int(dev['default_samplerate'])} Hz){mark}", flush=True)


async def run_text_mode(client: CoPilotClient) -> None:
    loop = asyncio.get_running_loop()
    print(f"Connecting to {client.ws_url} [TEXT MODE] (session={client.session_id})...", flush=True)
    async with connect(client.ws_url) as ws:
        await ws.send(json.dumps({"event": "register", "role": "voice_client", "session_id": client.session_id}))
        recv_task = asyncio.create_task(_recv_loop(ws, client))
        print(
            "Connected! Say 'Hey Jarvis' (shortcuts: /news, /weather, /voice male_1, /repeat, /reset, /stop, !<urgent>, q).\n",
            flush=True,
        )
        try:
            while True:
                rem = max(0.0, client.active_until - time.monotonic())
                prompt = f"Jarvis [ACTIVE {rem:2.0f}s]> " if rem > 0 else "Jarvis [IDLE]>       "
                line = (await loop.run_in_executor(None, input, prompt)).strip()
                if line.lower() == "q":
                    client.cancel_active_turn(close_window=True)
                    break
                await client.handle_utterance(ws, line)
        finally:
            recv_task.cancel()


async def run_mic_mode(client: CoPilotClient, no_denoise: bool, device: int | None, silence_s: float) -> None:
    try:
        import numpy as np
        import sounddevice as sd
        from moonshine_voice import Transcriber, TranscriptEventListener, get_model_for_language
        from stt_driving import SAMPLE_RATE, BLOCK_DURATION_S, DEFAULT_KEYTERMS_FILE, clean_transcript, load_keyterms_config
    except ImportError as exc:
        print(
            f"[ERROR] Missing STT package: {exc}\n"
            "Install with: python -m pip install -r speech-recognition/requirements.txt",
            file=sys.stderr,
            flush=True,
        )
        return

    df_stream = None
    if not no_denoise:
        print("[INIT] Loading DeepFilterNet3 denoiser...", flush=True)
        from deepfilter_stream import DeepFilterModel
        df_stream = DeepFilterModel().new_stream(atten_lim_db=18.0)

    keyterms, boost = load_keyterms_config(DEFAULT_KEYTERMS_FILE)
    for extra in ("Hey Jarvis", "Jarvis", "stop talking", "be quiet", "weather", "news", "reset", "stop", "repeat"):
        if extra not in keyterms:
            keyterms.append(extra)

    print("[INIT] Loading Moonshine English model...", flush=True)
    model_path, model_arch = get_model_for_language("en")
    transcriber = Transcriber(
        model_path=model_path,
        model_arch=model_arch,
        options={"keyterm_boost": boost} if keyterms else None,
    )
    if keyterms and hasattr(transcriber, "set_keyterms"):
        transcriber.set_keyterms(keyterms)

    dev_idx = device if device is not None else sd.default.device[0]
    dev_info = sd.query_devices(dev_idx, "input")
    print(f"[MIC] Using device [{dev_idx}]: {dev_info['name']} | silence concat={silence_s:.2f}s | barge-in=ON", flush=True)

    loop = asyncio.get_running_loop()
    utterance_queue: asyncio.Queue[str] = asyncio.Queue()
    aggregator = SpeechAggregator(loop, utterance_queue, silence_s=silence_s)
    barge_state: dict[str, str | None] = {"mode": None}

    class _Listener(TranscriptEventListener):
        def on_line_text_changed(self, event) -> None:
            t = event.line.text.strip()
            if not t:
                return

            if client.is_busy_output:
                if barge_state["mode"] is None:
                    hit = detect_barge_in(t, client.wake_words, client.tts.current_text)
                    if hit is not None:
                        barge_state["mode"] = hit
                        client.cancel_active_turn(close_window=(hit == "stop"))
                        aggregator.reset_and_drain_threadsafe()
                        print(f"\n[BARGE-IN:{hit.upper()}] Interrupted Jarvis -> {t}", flush=True)
                elif barge_state["mode"] == "wake":
                    print(f"\r[HEARING] {t:<80}", end="", flush=True)
                return

            aggregator.on_partial_threadsafe()
            print(f"\r[HEARING] {t:<80}", end="", flush=True)

        def on_line_completed(self, event) -> None:
            raw = event.line.text.strip()
            mode = barge_state["mode"]
            barge_state["mode"] = None

            if not raw:
                return

            cleaned = clean_transcript(raw)
            if not cleaned:
                if not client.is_busy_output:
                    print(f"\n[STT - dropped] {raw}", flush=True)
                return

            # Case 1: Barge-in already started during partial speech
            if mode == "stop":
                client.cancel_active_turn(close_window=True)
                aggregator.reset_and_drain_threadsafe()
                print(f"\n[BARGE-IN] Stopped cleanly ({cleaned}).", flush=True)
                return
            if mode == "wake":
                print(f"\n[STT] {cleaned}", flush=True)
                aggregator.on_completed_threadsafe(cleaned)
                return

            # Case 2: Jarvis is speaking OR waiting for server -> ONLY allow barge-in, drop everything else
            if client.is_busy_output:
                hit = detect_barge_in(cleaned, client.wake_words, client.tts.current_text)
                if hit == "stop":
                    client.cancel_active_turn(close_window=True)
                    aggregator.reset_and_drain_threadsafe()
                    print(f"\n[BARGE-IN] Stopped cleanly ({cleaned}).", flush=True)
                    return
                if hit == "wake":
                    client.cancel_active_turn(close_window=False)
                    aggregator.reset_and_drain_threadsafe()
                    print(f"\n[BARGE-IN:WAKE] {cleaned}", flush=True)
                    aggregator.on_completed_threadsafe(cleaned)
                    return
                tag = "ECHO" if client.tts.is_speaking.is_set() else "BUSY"
                print(f"\n[{tag} - ignored] {cleaned}", flush=True)
                return

            # Case 3: Trailing acoustic echo right after TTS finished
            since_tts = time.monotonic() - client.tts.last_finished_t
            if since_tts <= ECHO_TAIL_WINDOW_S and is_trailing_echo(cleaned, client.tts.last_text, client.wake_words):
                print(f"\n[ECHO - ignored] {cleaned}", flush=True)
                return

            # Case 4: Normal driver speech
            print(f"\n[STT] {cleaned}", flush=True)
            aggregator.on_completed_threadsafe(cleaned)

    transcriber.add_listener(_Listener())
    audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=MAX_AUDIO_QUEUE_CHUNKS)
    stop_audio = threading.Event()

    def _mic_worker() -> None:
        def callback(indata, frames, time_info, status):
            chunk = indata[:, 0].copy()
            try:
                audio_q.put_nowait(chunk)
            except queue.Full:
                try:
                    audio_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    audio_q.put_nowait(chunk)
                except queue.Full:
                    pass

        try:
            transcriber.start()
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=int(SAMPLE_RATE * BLOCK_DURATION_S),
                device=device,
                callback=callback,
            ):
                print("[MIC] Microphone is LIVE! Say 'Hey Jarvis' (interrupt anytime with 'Stop' / 'Hey Jarvis').\n", flush=True)
                while not stop_audio.is_set():
                    try:
                        chunk = audio_q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if df_stream is not None:
                        chunk = df_stream.process(chunk, sr=SAMPLE_RATE)
                    if len(chunk) > 0:
                        transcriber.add_audio(chunk, SAMPLE_RATE)
        except Exception:
            print("\n[MIC ERROR] Microphone thread failed:", file=sys.stderr, flush=True)
            traceback.print_exc()
        finally:
            if df_stream is not None:
                tail = df_stream.flush()
                if len(tail) > 0:
                    transcriber.add_audio(tail, SAMPLE_RATE)
            transcriber.stop()
            transcriber.close()

    print(f"[WS] Connecting to {client.ws_url} (session={client.session_id})...", flush=True)
    async with connect(client.ws_url) as ws:
        await ws.send(json.dumps({"event": "register", "role": "voice_client", "session_id": client.session_id}))
        recv_task = asyncio.create_task(_recv_loop(ws, client))
        mic_thread = threading.Thread(target=_mic_worker, name="mic-stt", daemon=True)
        mic_thread.start()
        try:
            while True:
                utterance = await utterance_queue.get()
                await client.handle_utterance(ws, utterance)
        finally:
            recv_task.cancel()
            client.cancel_active_turn(close_window=True)
            stop_audio.set()
            mic_thread.join(timeout=3.0)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    parser = argparse.ArgumentParser(description="Dual-Tier Co-Pilot Voice/Text Client with Barge-In & S1 Push")
    parser.add_argument("--ws", default=DEFAULT_WS_URL)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--session", default=DEFAULT_SESSION_ID, help=f"Session ID (default: {DEFAULT_SESSION_ID})")
    parser.add_argument("--list-devices", action="store_true", help="List available microphones and exit")
    parser.add_argument("--mic", action="store_true", help="Use live microphone")
    parser.add_argument("--text", action="store_true", help="Use keyboard mode (default)")
    parser.add_argument("--no-denoise", action="store_true", help="Disable DeepFilterNet3 in --mic mode")
    parser.add_argument("--device", type=int, default=None, help="Microphone device index")
    parser.add_argument("--silence-s", type=float, default=SILENCE_CONCAT_S, help="Silence wait before sending (default: 0.75s)")
    parser.add_argument("--voice", default="female_1", help="TTS voice: male_1, male_2, female_1, female_2")
    parser.add_argument("--no-tts", action="store_true", help="Disable spoken audio output (print only)")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    profile = load_profile(args.profile)
    tts = TTSPlayer(enabled=not args.no_tts, voice=args.voice)
    client = CoPilotClient(args.ws, profile, tts, session_id=args.session)
    try:
        if args.mic:
            asyncio.run(run_mic_mode(client, args.no_denoise, args.device, args.silence_s))
        else:
            asyncio.run(run_text_mode(client))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()