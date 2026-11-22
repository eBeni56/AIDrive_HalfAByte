"""TEXT-TO-SPEECH: speaks the warnings and the coach's lines through the speakers with Phonon (ptts: offline,
CPU only, ~65 MB downloaded once from Hugging Face on the first run).

One worker thread owns the model and one audio stream that stays open. say(text, priority, max_age_s):
  - a line MORE urgent than the one being spoken interrupts it (within ~0.1 s);
  - otherwise it waits; when the speaker is free, the most urgent waiting line is spoken (oldest first);
  - a line still waiting after max_age_s is dropped: an old warning is worse than none;
  - the same text is never waiting twice.
drop_below(priority) removes the waiting lines below a priority (a tip once a warning came in); drop_above(priority)
the ones above it (the warnings at the end of a run); stop() cuts everything (a new simulation is prepared).
The audio of every finished line is cached, so a repeated warning starts at once.
"""
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

VOICES = {"female_1": "Zoey", "female_2": "Juniper", "male_1": "Easton", "male_2": "Micah"}
TTS_THREADS = 4          # leave the other cores to the driver monitor
SLICE_S = 0.1            # audio is written in slices this long: how fast an interruption cuts in
CACHE_LINES = 64

log = logging.getLogger("voice")


@dataclass(order=True)
class Line:
    sort_key: tuple = field(init=False, repr=False)
    text: str = field(compare=False)
    priority: int = field(compare=False)
    max_age_s: float = field(compare=False)
    created: float = field(default_factory=time.monotonic, compare=False)

    def __post_init__(self):
        self.sort_key = (-self.priority, self.created)   # most urgent first, then oldest


def speakable(text):
    """Small fixes so units are read out, not spelled."""
    return text.replace("%", " percent").replace("&", " and ")


class Voice:
    def __init__(self, voice="female_1", mute=False):
        self.voice = VOICES.get(voice.lower(), voice)
        self.mute = mute
        self.cond = threading.Condition()
        self.waiting = []
        self.current = None          # the Line being spoken
        self.interrupt = False
        self.cache = OrderedDict()   # text -> numpy array (finished lines only)
        self.tts = self.stream = self.np = None
        threading.Thread(target=self._run, name="voice", daemon=True).start()

    # ---- called from any thread ----

    def say(self, text, priority, max_age_s):
        text = speakable(text.strip())
        if not text:
            return
        with self.cond:
            if self.current is not None and priority > self.current.priority:
                self.interrupt = True
            self.waiting = [w for w in self.waiting if w.text != text]
            self.waiting.append(Line(text, priority, max_age_s))
            self.cond.notify()

    def drop_below(self, priority):
        with self.cond:
            self.waiting = [w for w in self.waiting if w.priority >= priority]

    def stop(self):
        """Stop talking at once (a new simulation is being prepared): the line being spoken is cut, nothing
        waiting is spoken."""
        with self.cond:
            self.waiting = []
            if self.current is not None:
                self.interrupt = True

    def drop_above(self, priority):
        """Remove the waiting lines above a priority (the warnings once the run is over); the line being spoken
        is finished."""
        with self.cond:
            self.waiting = [w for w in self.waiting if w.priority <= priority]

    # ---- the worker thread ----

    def _load(self):
        if self.mute:
            log.info("muted: lines are only printed")
            return
        try:
            import numpy as np
            import ptts
            import sounddevice as sd
            ptts.set_num_threads(TTS_THREADS)
            t0 = time.perf_counter()
            self.tts = ptts.TTS(lang="en")
            self.stream = sd.OutputStream(samplerate=self.tts.sample_rate, channels=1, dtype="float32")
            self.stream.start()
            self.np = np
            log.info("Phonon voice %s ready in %.1f s", self.voice, time.perf_counter() - t0)
        except Exception as e:   # no model download, no audio device, missing package: keep running, print only
            log.error("text-to-speech unavailable, lines are only printed: %s", e)
            self.mute = True

    def _next(self):
        with self.cond:
            while True:
                now = time.monotonic()
                for w in self.waiting:
                    if now - w.created > w.max_age_s:
                        log.info("too old, not spoken: %s", w.text)
                self.waiting = sorted(w for w in self.waiting if now - w.created <= w.max_age_s)
                if self.waiting:
                    self.current = self.waiting.pop(0)
                    self.interrupt = False
                    return self.current
                self.cond.wait()

    def _run(self):
        self._load()
        while True:
            line = self._next()
            log.info("SAY p%d: %s", line.priority, line.text)
            if not self.mute:
                try:
                    self._play(line.text)
                except Exception as e:   # an audio error must not stop the voice
                    log.error("could not speak %r: %s", line.text, e)
            with self.cond:
                self.current = None

    def _play(self, text):
        audio = self.cache.get(text)
        if audio is not None:
            self.cache.move_to_end(text)
            self._write(audio)
            return
        parts = []
        with self.tts.stream(text, voice=self.voice) as chunks:
            for chunk in chunks:
                a = self.np.asarray(chunk, dtype=self.np.float32).reshape(-1)
                parts.append(a)
                if not self._write(a):
                    return                       # interrupted: the line is not finished, not cached
        self.cache[text] = self.np.concatenate(parts) if parts else self.np.zeros(0, self.np.float32)
        while len(self.cache) > CACHE_LINES:
            self.cache.popitem(last=False)

    def _write(self, audio):
        """Write in short slices; False (and the buffered audio discarded) when interrupted."""
        step = int(self.tts.sample_rate * SLICE_S)
        for i in range(0, len(audio), step):
            if self.interrupt:
                self.stream.abort()              # drop what is still buffered, then reopen
                self.stream.start()
                log.info("interrupted (a more urgent line, or stopped)")
                return False
            self.stream.write(audio[i:i + step].reshape(-1, 1))
        return True
