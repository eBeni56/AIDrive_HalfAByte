#!/usr/bin/env python3
"""Minimal offline English TTS using Phonon 31M (ptts, Apache-2.0) + sounddevice."""

from __future__ import annotations

from collections.abc import Iterable
import queue
import re
import sys
import threading
import numpy as np
import ptts
import sounddevice as sd

VOICES = {
    "female_1": "Zoey",
    "female_2": "Juniper",
    "male_1": "Easton",
    "male_2": "Micah",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class Speaker:
    def __init__(self, voice: str = "female_1"):
        self.tts = ptts.TTS(lang="en")
        self.voice = VOICES.get(voice.lower(), voice)

    def set_voice(self, voice: str) -> None:
        self.voice = VOICES.get(voice.lower(), voice)

    def _play(self, out: sd.OutputStream, text: str) -> None:
        with self.tts.stream(text, voice=self.voice) as audio:
            for chunk in audio:
                pcm = np.asarray(chunk, dtype=np.float32).reshape(-1, 1)
                if len(pcm) > 0:
                    out.write(pcm)

    def speak(self, text: str) -> None:
        """Read a complete string aloud."""
        text = text.strip()
        if not text:
            return
        with sd.OutputStream(samplerate=self.tts.sample_rate, channels=1, dtype="float32") as out:
            self._play(out, text)

    def speak_stream(self, tokens: Iterable[str]) -> None:
        """Read an LLM token stream aloud, starting as soon as the first sentence finishes."""
        q: queue.Queue[str | None] = queue.Queue()

        def worker() -> None:
            with sd.OutputStream(samplerate=self.tts.sample_rate, channels=1, dtype="float32") as out:
                while (sentence := q.get()) is not None:
                    self._play(out, sentence)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        buf = ""
        try:
            for token in tokens:
                buf += token
                parts = _SENTENCE_SPLIT.split(buf)
                if len(parts) > 1:
                    for done in parts[:-1]:
                        if done.strip():
                            q.put(done.strip())
                    buf = parts[-1]
            if buf.strip():
                q.put(buf.strip())
        finally:
            q.put(None)
            t.join()


if __name__ == "__main__":
    voice = sys.argv[1] if len(sys.argv) > 1 else "female_1"
    speaker = Speaker(voice)
    print(f"Ready ({speaker.voice}). Type text to speak, '/voice <male_1|male_2|female_1|female_2>', or Ctrl+C to quit.")

    while True:
        try:
            line = input("LLM> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.startswith("/voice "):
            speaker.set_voice(line.split(maxsplit=1)[1].strip())
            print(f"Voice -> {speaker.voice}")
        elif line:
            speaker.speak(line)