"""
Co-Pilot Voice & Text Client (Wake-word + 0.75s Silence Concat + 15s Active Window)
===================================================================================
Usage:
  python -u copilot/voice_client.py --text
  python -u copilot/voice_client.py --list-devices
  python -u copilot/voice_client.py --mic
  python -u copilot/voice_client.py --mic --device 1 --no-denoise
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import queue
import re
import sys
import threading
import time
import traceback
from typing import Any
import uuid

from websockets.asyncio.client import connect

COPILOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = COPILOT_DIR.parent
STT_DIR = REPO_ROOT / "speech-recognition"

if str(STT_DIR) not in sys.path:
    sys.path.insert(0, str(STT_DIR))

DEFAULT_WS_URL = "ws://localhost:8766"
DEFAULT_PROFILE_PATH = COPILOT_DIR / "driver_profile.json"

SILENCE_CONCAT_S = 0.75  # Wait 0.75s of silence after last STT line before sending

STOP_PHRASES = {
    "/stop", "stop", "stop listening", "cancel", "never mind",
    "nevermind", "be quiet", "quiet", "shut up", "dismiss",
}
RESET_PHRASES = {
    "/reset", "reset", "reset memory", "clear memory", "forget everything", "new conversation",
}
REPEAT_PHRASES = {
    "/repeat", "repeat", "repeat that", "say that again", "what did you say", "pardon",
}
THANKS_PHRASES = {"thanks", "thank you", "thanks jarvis", "thank you jarvis"}
# Short backchannels / STT blips that should NOT trigger a full LLM reply while active
IGNORE_BACKCHANNELS = {
    "yeah", "yes", "yep", "nope", "no", "ok", "okay", "right", "alright",
    "sure", "cool", "nice", "good", "fine", "oh", "and so", "so", "well", "bye",
}


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


def speak_tts(text: str, priority: str = "normal") -> None:
    tag = "URGENT TTS" if priority == "high" else "TTS"
    print(f"\n[{tag}] {text}\n", flush=True)


def extract_wake_word(text: str, wake_words: list[str]) -> tuple[bool, str]:
    """Matches a wake word ANYWHERE in the sentence and returns (matched, text_after_wake_word)."""
    for ww in sorted(wake_words, key=len, reverse=True):
        words = [re.escape(w) for w in ww.strip().lower().split() if w]
        if not words:
            continue
        pattern = r"\b" + r"[\s,.:;!?-]+".join(words) + r"\b[\s,.:;!?-]*"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            remainder = text[match.end() :].strip()
            return True, remainder
    return False, text.strip()


class SpeechAggregator:
    """Buffers consecutive completed STT lines and concatenates them until `silence_s` passes."""

    def __init__(self, loop: asyncio.AbstractEventLoop, out_queue: asyncio.Queue[str], silence_s: float = SILENCE_CONCAT_S) -> None:
        self.loop = loop
        self.out_queue = out_queue
        self.silence_s = silence_s
        self._parts: list[str] = []
        self._timer: asyncio.TimerHandle | None = None

    def on_partial_threadsafe(self) -> None:
        self.loop.call_soon_threadsafe(self._on_partial)

    def on_completed_threadsafe(self, text: str) -> None:
        self.loop.call_soon_threadsafe(self._on_completed, text)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_partial(self) -> None:
        # User is still actively speaking: push back flush so we don't split mid-sentence
        if self._parts:
            self._cancel_timer()
            self._timer = self.loop.call_later(max(self.silence_s * 2.0, 1.5), self._flush)

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
    def __init__(self, ws_url: str, profile: dict[str, Any], session_id: str | None = None) -> None:
        self.ws_url = ws_url
        self.profile = profile
        self.session_id = session_id or f"driver-{uuid.uuid4().hex[:6]}"
        self.wake_words: list[str] = [str(w).lower() for w in profile.get("wake_words", ["hey jarvis"])]
        self.listen_window_s = float(profile.get("listen_window_s", 15.0))
        self.default_city = str(profile.get("default_city", "Cluj-Napoca"))
        self.active_until = 0.0
        self.last_spoken: str | None = None
        self._req_counter = 0

        # No hardcoded "weather": "clear" so weather queries always use real Open-Meteo data
        self.vehicle: dict[str, Any] = {"speed_kmh": 95, "battery_pct": 62}
        self.driver: dict[str, Any] = {"face_detected": True, "drowsiness_level": "ALERT", "eyes_state": "OPEN"}

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"req-{self._req_counter}"

    @property
    def is_listening(self) -> bool:
        return time.monotonic() < self.active_until

    def open_window(self) -> None:
        self.active_until = time.monotonic() + self.listen_window_s

    def close_window(self) -> None:
        self.active_until = 0.0

    def _say(self, text: str, priority: str = "normal") -> None:
        self.last_spoken = text
        speak_tts(text, priority=priority)

    def _base_payload(self, driver_message: str) -> dict[str, Any]:
        return {
            "request_id": self._next_id(),
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

    async def _send_and_recv(self, ws, payload: dict[str, Any]) -> dict[str, Any]:
        await ws.send(json.dumps(payload, ensure_ascii=False))
        return json.loads(await ws.recv())

    async def handle_utterance(self, ws, raw_text: str) -> None:
        text = raw_text.strip()
        if not text:
            return

        # Direct urgent shortcut in text mode: "!eyes closed in rain"
        if text.startswith("!"):
            reply = await self._send_and_recv(ws, {
                "request_id": self._next_id(),
                "session_id": self.session_id,
                "mode": "urgent",
                "situation": text[1:].strip(),
                "goal": "Give the immediate safe driving reflex.",
            })
            if reply.get("spoken_text"):
                self._say(reply["spoken_text"], priority="high")
            return

        # Direct manual slash shortcuts (work even when IDLE)
        low = text.lower().strip(" .!?,")
        if low.startswith("/news") or low.startswith("/weather"):
            parts = text.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1].strip() if len(parts) > 1 else ""
            payload = self._base_payload(f"Run {cmd} {args}".strip())
            payload["force_command"] = cmd
            payload["force_args"] = args
            reply = await self._send_and_recv(ws, payload)
            if reply.get("tool_used"):
                print(f"[SERVER TOOL] {reply['tool_used']}", flush=True)
            if reply.get("spoken_text"):
                self._say(reply["spoken_text"])
            self.open_window()
            return

        if low in RESET_PHRASES:
            reply = await self._send_and_recv(ws, {
                "event": "reset_session",
                "request_id": self._next_id(),
                "session_id": self.session_id,
            })
            self._say(reply.get("spoken_text") or "Conversation memory cleared.")
            return

        if low in REPEAT_PHRASES and self.is_listening:
            self._say(self.last_spoken or "I have not said anything yet.")
            self.open_window()
            return

        if low in STOP_PHRASES:
            if self.is_listening:
                self.close_window()
                self._say("Okay, standing by.")
            return

        # Wake-word gate (matches anywhere in utterance) + 15s active window
        woke, remainder = extract_wake_word(text, self.wake_words)
        if woke:
            self.open_window()
            if not remainder:
                print(f"[WAKE] Jarvis listening for {self.listen_window_s:.0f}s...", flush=True)
                self._say("Yes, I am listening.")
                return
            text = remainder
        elif not self.is_listening:
            print(f"[IDLE - ignored] {text}", flush=True)
            return

        low_cmd = re.sub(r"[^a-z0-9/\s]", "", text.lower()).strip()

        # Local zero-latency handlers (prevent LLM spam on backchannels)
        if low_cmd in STOP_PHRASES or re.search(r"\b(stop listening|stop stop|cancel|be quiet)\b", low_cmd):
            self.close_window()
            self._say("Okay, standing by.")
            return
        if low_cmd in RESET_PHRASES:
            reply = await self._send_and_recv(ws, {
                "event": "reset_session",
                "request_id": self._next_id(),
                "session_id": self.session_id,
            })
            self.open_window()
            self._say(reply.get("spoken_text") or "Conversation memory cleared.")
            return
        if low_cmd in REPEAT_PHRASES:
            self._say(self.last_spoken or "I have not said anything yet.")
            self.open_window()
            return
        if low_cmd in THANKS_PHRASES:
            self._say("You are welcome.")
            self.close_window()
            return
        if low_cmd in IGNORE_BACKCHANNELS or len(low_cmd.split()) == 1 and len(low_cmd) <= 3:
            print(f"[BACKCHANNEL - skipped] {text}", flush=True)
            return

        # Send clean, merged utterance to server
        reply = await self._send_and_recv(ws, self._base_payload(text))
        if reply.get("tool_used"):
            print(f"[SERVER TOOL] {reply['tool_used']}", flush=True)

        action = reply.get("action")
        if action == "stop":
            self.close_window()
            if reply.get("spoken_text"):
                self._say(reply["spoken_text"])
            return

        if reply.get("spoken_text"):
            self._say(reply["spoken_text"], priority=reply.get("priority", "normal"))
            self.open_window()


# =============================================================================
# RUNNERS
# =============================================================================
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
        print("Connected! Say 'Hey Jarvis' to open the 15s window (shortcuts: /news, /weather, /repeat, /reset, /stop, !<urgent>, q).\n", flush=True)
        while True:
            rem = max(0.0, client.active_until - time.monotonic())
            prompt = f"Jarvis [ACTIVE {rem:2.0f}s]> " if rem > 0 else "Jarvis [IDLE]>       "
            line = (await loop.run_in_executor(None, input, prompt)).strip()
            if line.lower() == "q":
                break
            await client.handle_utterance(ws, line)


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
    for extra in ("Hey Jarvis", "Jarvis", "weather", "news", "reset", "stop", "repeat"):
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
    print(f"[MIC] Using device [{dev_idx}]: {dev_info['name']} | silence concat={silence_s:.2f}s", flush=True)

    loop = asyncio.get_running_loop()
    utterance_queue: asyncio.Queue[str] = asyncio.Queue()
    aggregator = SpeechAggregator(loop, utterance_queue, silence_s=silence_s)

    class _Listener(TranscriptEventListener):
        def on_line_text_changed(self, event) -> None:
            t = event.line.text.strip()
            if t:
                aggregator.on_partial_threadsafe()
                print(f"\r[HEARING] {t:<80}", end="", flush=True)

        def on_line_completed(self, event) -> None:
            raw = event.line.text.strip()
            print(flush=True)
            if not raw:
                return
            cleaned = clean_transcript(raw)
            if cleaned:
                print(f"[STT] {cleaned}", flush=True)
                aggregator.on_completed_threadsafe(cleaned)
            else:
                print(f"[STT - dropped] {raw}", flush=True)

    transcriber.add_listener(_Listener())
    audio_q: queue.Queue[np.ndarray] = queue.Queue()
    stop_audio = threading.Event()

    def _mic_worker() -> None:
        def callback(indata, frames, time_info, status):
            if status:
                print(f"\n[AUDIO WARNING] {status}", file=sys.stderr, flush=True)
            audio_q.put(indata[:, 0].copy())

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
                print("[MIC] Microphone is LIVE! Speak now (say 'Hey Jarvis')...\n", flush=True)
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
        print(f"[WS] Connected to {client.ws_url}!", flush=True)
        mic_thread = threading.Thread(target=_mic_worker, name="mic-stt", daemon=True)
        mic_thread.start()
        try:
            while True:
                utterance = await utterance_queue.get()
                await client.handle_utterance(ws, utterance)
        finally:
            stop_audio.set()
            mic_thread.join(timeout=3.0)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    parser = argparse.ArgumentParser(description="Dual-Tier Co-Pilot Voice/Text Client")
    parser.add_argument("--ws", default=DEFAULT_WS_URL)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--list-devices", action="store_true", help="List available microphones and exit")
    parser.add_argument("--mic", action="store_true", help="Use live microphone")
    parser.add_argument("--text", action="store_true", help="Use keyboard mode (default)")
    parser.add_argument("--no-denoise", action="store_true", help="Disable DeepFilterNet3 in --mic mode")
    parser.add_argument("--device", type=int, default=None, help="Microphone device index")
    parser.add_argument("--silence-s", type=float, default=SILENCE_CONCAT_S, help="Silence wait before sending (default: 0.75s)")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    profile = load_profile(args.profile)
    client = CoPilotClient(args.ws, profile)
    try:
        if args.mic:
            asyncio.run(run_mic_mode(client, args.no_denoise, args.device, args.silence_s))
        else:
            asyncio.run(run_text_mode(client))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()