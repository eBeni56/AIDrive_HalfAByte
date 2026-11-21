#!/usr/bin/env python3
"""
Local English Speech-to-Text for driving environments.
Combines DeepFilterNet3 ONNX streaming noise suppression (deepfilter-stream),
Moonshine Voice (moonshine-voice) with car-control keyterms, and a transcript
cleaner that strips tiny noise blips, fillers, and stuttering before saving/sending.

Install dependencies (CPU-only, no PyTorch required):
    python -m pip install -r requirements.txt

Quick usage:
    python stt_driving.py
    python stt_driving.py --no-keyterms -o without_keyterms.txt
    python stt_driving.py --no-keyterms --raw-text -o before_version.txt
    python stt_driving.py --keyterm-boost 3.0 -o boost3.txt
    python stt_driving.py --no-denoise -o raw_audio.txt
    python stt_driving.py --list-devices
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import queue
import re
import sys
import numpy as np
import sounddevice as sd
from moonshine_voice import (
    Transcriber,
    TranscriptEventListener,
    get_model_for_language,
)

SAMPLE_RATE = 48000       # Native rate for DeepFilterNet3; Moonshine resamples internally
BLOCK_DURATION_S = 0.05   # 50 ms audio chunks
DEFAULT_KEYTERMS_FILE = Path(__file__).with_name("keyterms.json")

# Standalone verbal fillers / hesitation sounds to drop
FILLER_WORDS = {
    "uh", "uhh", "uhhh", "um", "umm", "ummm", "er", "err", "erm",
    "ah", "ahh", "ahhh", "hmm", "hmmm", "hm", "mm", "mmm", "mhm", "huh",
}

# Short single words that are genuine commands/answers and must NOT be dropped
ALLOWED_SHORT_WORDS = {
    "yes", "no", "ok", "okay", "stop", "go", "help", "mute",
    "play", "next", "back", "up", "down", "on", "off", "call", "home",
}

# Number words we should NOT collapse if repeated (e.g., "five five")
NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
}

_FILLER_ALT = "|".join(sorted(FILLER_WORDS, key=len, reverse=True))
# Remove bracketed mid-sentence fillers like ", uh," along with both commas
_BRACKETED_FILLER_PATTERN = re.compile(
    r",\s*(?:" + _FILLER_ALT + r")\b[,.;:]*",
    re.IGNORECASE,
)
_FILLER_PATTERN = re.compile(
    r"(?:^|(?<=\s))(?:" + _FILLER_ALT + r")(?=$|[\s,.;:!?-])[,.;:]*",
    re.IGNORECASE,
)
# Cut-off hyphenated stutters like "t-turn" or "ra- radio" -> keep the full word
_PREFIX_STUTTER_PATTERN = re.compile(r"\b([a-zA-Z]{1,3})-\s*(\1[a-zA-Z]+)\b", re.IGNORECASE)
# Repeated single word: "turn turn", "the, the", "I I I"
_SINGLE_WORD_REPEAT = re.compile(r"\b([a-zA-Z']+)(?:[\s,]+\1\b)+", re.IGNORECASE)
# Repeated 2-word phrase: "turn on turn on", "can you, can you"
_TWO_WORD_REPEAT = re.compile(
    r"\b([a-zA-Z']+\s+[a-zA-Z']+)(?:[\s,]+\1\b)+",
    re.IGNORECASE,
)


def clean_transcript(text: str, min_chars: int = 3) -> str | None:
    """
    Remove noise blips, hesitation fillers, and stutters so downstream LLMs
    receive clean sentences. Returns None if the line should be ignored.
    """
    cleaned = text.strip()
    if not cleaned:
        return None

    # 1. Strip standalone filler sounds ("uh", "um", "hmm", etc.)
    cleaned = _BRACKETED_FILLER_PATTERN.sub(" ", cleaned)
    cleaned = _FILLER_PATTERN.sub(" ", cleaned)

    # 2. Collapse cut-off prefix stutters ("t-turn" -> "turn", "ra- radio" -> "radio")
    cleaned = _PREFIX_STUTTER_PATTERN.sub(r"\2", cleaned)

    # 3. Collapse repeated 2-word phrases ("turn on turn on the AC" -> "turn on the AC")
    cleaned = _TWO_WORD_REPEAT.sub(r"\1", cleaned)

    # 4. Collapse repeated single words ("the the radio" -> "the radio"), skipping numbers
    def _dedup_word(match: re.Match) -> str:
        word = match.group(1)
        if word.lower() in NUMBER_WORDS:
            return match.group(0)
        return word

    cleaned = _SINGLE_WORD_REPEAT.sub(_dedup_word, cleaned)

    # 5. Tidy spacing and stray punctuation left behind after filler removal
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[\s,.;:!?-]+", "", cleaned).strip()
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:]){2,}", r"\1", cleaned)

    # 6. Drop tiny noise blips (punctuation-only, single letters, stray 1-2 char junk)
    words = re.findall(r"[a-zA-Z0-9']+", cleaned)
    if not words:
        return None

    if len(words) == 1:
        only_word = words[0].lower()
        if only_word in FILLER_WORDS:
            return None
        if len(only_word) < min_chars and only_word not in ALLOWED_SHORT_WORDS:
            return None

    # Re-capitalize first letter if filler removal left a lowercase start
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]

    return cleaned


def load_keyterms_config(config_path: Path) -> tuple[list[str], float]:
    """Load keyterms list and default boost from a JSON config file."""
    if not config_path.exists():
        print(
            f"[Warning] Keyterms file not found at {config_path}. Running without keyterms.",
            file=sys.stderr,
        )
        return [], 2.0

    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    raw_terms = data.get("keyterms", []) if isinstance(data, dict) else data
    boost = float(data.get("keyterm_boost", 2.0)) if isinstance(data, dict) else 2.0

    # Moonshine requires terms without commas
    terms = []
    for item in raw_terms:
        term = str(item).replace(",", " ").strip()
        if term:
            terms.append(term)

    return terms, boost


class FileTranscriptListener(TranscriptEventListener):
    """Displays live partial speech, cleans finished lines, and saves to a .txt file."""

    def __init__(
        self,
        output_file,
        plain_text: bool = False,
        clean_text: bool = True,
        min_chars: int = 3,
    ):
        super().__init__()
        self._file = output_file
        self._plain_text = plain_text
        self._clean_text = clean_text
        self._min_chars = min_chars
        self._last_preview_len = 0
        self.completed_lines = 0
        self.dropped_blips = 0

    def _clear_preview(self):
        if self._last_preview_len > 0:
            print("\r" + " " * self._last_preview_len + "\r", end="", flush=True)
            self._last_preview_len = 0

    def on_line_text_changed(self, event):
        text = event.line.text.strip()
        if not text:
            return
        preview = f"Listening: {text}"
        pad = max(0, self._last_preview_len - len(preview))
        print(f"\r{preview}{' ' * pad}", end="", flush=True)
        self._last_preview_len = len(preview)

    def on_line_completed(self, event):
        raw_text = event.line.text.strip()
        if not raw_text:
            return

        self._clear_preview()

        if self._clean_text:
            final_text = clean_transcript(raw_text, min_chars=self._min_chars)
            if final_text is None:
                self.dropped_blips += 1
                print(f"Ignored:   {raw_text}")
                return
        else:
            final_text = raw_text

        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = final_text if self._plain_text else f"[{timestamp}] {final_text}"

        print(f"Saved:     {formatted}")
        self._file.write(formatted + "\n")
        self._file.flush()
        self.completed_lines += 1


def parse_args():
    parser = argparse.ArgumentParser(
        description="Local English STT (Moonshine Voice + DeepFilterNet3 + Keyterms + Cleaner) -> .txt"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("transcript.txt"),
        help="Path to the output .txt file (default: transcript.txt)",
    )
    parser.add_argument(
        "--no-denoise",
        action="store_true",
        help="Disable DeepFilterNet3 noise suppression (useful for A/B testing).",
    )
    parser.add_argument(
        "--atten-lim-db",
        type=float,
        default=18.0,
        help=(
            "Max noise suppression in dB (default: 18.0). "
            "Set to 0 for unlimited suppression. 12-20 dB usually preserves STT accuracy best."
        ),
    )
    parser.add_argument(
        "--keyterms-file",
        type=Path,
        default=DEFAULT_KEYTERMS_FILE,
        help=f"JSON config containing car control keyterms (default: {DEFAULT_KEYTERMS_FILE.name}).",
    )
    parser.add_argument(
        "--no-keyterms",
        action="store_true",
        help="Disable keyterm biasing (useful for A/B accuracy testing).",
    )
    parser.add_argument(
        "--keyterm-boost",
        type=float,
        default=None,
        help=(
            "Override keyterm boost strength from keyterms.json "
            "(2.0 = safe default, 3.0 = stronger bias on controls, avoid >= 4.0)."
        ),
    )
    parser.add_argument(
        "--raw-text",
        action="store_true",
        help="Disable blip/filler/stutter filtering and save raw Moonshine output.",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=3,
        help="Drop unknown single-word blips shorter than this many characters (default: 3).",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Write plain transcript lines without [HH:MM:SS] timestamps or session headers.",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Microphone device index (run --list-devices to inspect available inputs).",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices and exit.",
    )
    parser.add_argument(
        "--save-wav-dir",
        type=Path,
        default=None,
        help="Optional folder to save the exact audio Moonshine received (for debugging noise).",
    )
    return parser.parse_args()


def list_input_devices():
    devices = sd.query_devices()
    print("Available audio input devices:")
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            default_mark = " (default)" if idx == sd.default.device[0] else ""
            print(f"  [{idx}] {dev['name']} ({int(dev['default_samplerate'])} Hz){default_mark}")


def main():
    args = parse_args()

    if args.list_devices:
        list_input_devices()
        return

    # 1. Load optional DeepFilterNet3 ONNX streaming denoiser
    df_stream = None
    if not args.no_denoise:
        try:
            from deepfilter_stream import DeepFilterModel
        except ImportError:
            print(
                "Error: 'deepfilter-stream' is not installed.\n"
                "Run: python -m pip install deepfilter-stream\n"
                "Or run this script with --no-denoise to test Moonshine alone.",
                file=sys.stderr,
            )
            sys.exit(1)

        atten_lim = None if args.atten_lim_db <= 0 else args.atten_lim_db
        print(f"Loading DeepFilterNet3 ONNX denoiser (atten_lim_db={atten_lim})...")
        df_model = DeepFilterModel()
        df_stream = df_model.new_stream(atten_lim_db=atten_lim)
        denoise_label = f"DeepFilterNet3 (atten_lim_db={atten_lim})"
    else:
        denoise_label = "OFF (raw mic audio)"

    # 2. Load optional car-control keyterms config
    keyterms: list[str] = []
    keyterm_boost = 2.0
    if not args.no_keyterms:
        keyterms, config_boost = load_keyterms_config(args.keyterms_file)
        keyterm_boost = config_boost if args.keyterm_boost is None else args.keyterm_boost

    # 3. Load Moonshine English model (defaults to Medium Streaming, cached locally)
    print("Loading Moonshine English model (first run downloads and caches it locally)...")
    model_path, model_arch = get_model_for_language("en")

    options = {}
    if keyterms:
        options["keyterm_boost"] = keyterm_boost
    if args.save_wav_dir is not None:
        args.save_wav_dir.mkdir(parents=True, exist_ok=True)
        options["save_input_wav_path"] = str(args.save_wav_dir)

    transcriber = Transcriber(
        model_path=model_path,
        model_arch=model_arch,
        options=options if options else None,
    )

    keyterms_label = "OFF"
    if keyterms:
        if hasattr(transcriber, "set_keyterms"):
            try:
                transcriber.set_keyterms(keyterms)
                keyterms_label = f"ON ({len(keyterms)} terms, boost={keyterm_boost})"
            except Exception as exc:
                print(
                    f"[Warning] Could not enable keyterms ({exc}). Continuing without keyterms.",
                    file=sys.stderr,
                )
        else:
            print(
                "[Warning] Installed moonshine-voice version does not support set_keyterms(). "
                "Upgrade with: python -m pip install -U moonshine-voice",
                file=sys.stderr,
            )

    cleaner_label = "OFF (raw text)" if args.raw_text else f"ON (min_chars={args.min_chars})"

    # 4. Open transcript file for appending
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as out_file:
        if not args.plain:
            session_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            out_file.write(
                f"\n--- Session started {session_time} | Denoiser: {denoise_label} "
                f"| Keyterms: {keyterms_label} | Cleaner: {cleaner_label} ---\n"
            )
            out_file.flush()

        listener = FileTranscriptListener(
            out_file,
            plain_text=args.plain,
            clean_text=not args.raw_text,
            min_chars=args.min_chars,
        )
        transcriber.add_listener(listener)

        # 5. Decouple mic capture from inference with a thread-safe queue
        audio_queue: queue.Queue[np.ndarray] = queue.Queue()

        def mic_callback(indata, frames, time_info, status):
            if status:
                print(f"\n[Audio warning: {status}]", file=sys.stderr)
            audio_queue.put(indata[:, 0].copy())

        blocksize = int(SAMPLE_RATE * BLOCK_DURATION_S)
        transcriber.start()

        print(f"\nReady! Saving English transcripts to: {args.output.resolve()}")
        print(f"Noise suppression: {denoise_label}")
        print(f"Keyterms:          {keyterms_label}")
        print(f"Text cleaner:      {cleaner_label}")
        print("Speak into your microphone (press Ctrl+C to stop)...\n")

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                device=args.device,
                callback=mic_callback,
            ):
                while True:
                    raw_chunk = audio_queue.get()

                    if df_stream is not None:
                        clean_chunk = df_stream.process(raw_chunk, sr=SAMPLE_RATE)
                    else:
                        clean_chunk = raw_chunk

                    if len(clean_chunk) > 0:
                        transcriber.add_audio(clean_chunk, SAMPLE_RATE)

        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            if df_stream is not None:
                tail = df_stream.flush()
                if len(tail) > 0:
                    transcriber.add_audio(tail, SAMPLE_RATE)
            transcriber.stop()
            transcriber.close()
            print(
                f"Done. Saved {listener.completed_lines} line(s), "
                f"ignored {listener.dropped_blips} noise blip(s) -> {args.output.resolve()}"
            )


if __name__ == "__main__":
    main()