# Offline English TTS

A small offline English text-to-speech module using Phonon 31M (`ptts`, Apache-2.0) and `sounddevice`.
Runs locally on the CPU with no PyTorch and no cloud API: pass it a full string or an LLM token stream and it plays speech straight to your speakers.
The first run needs internet to download and cache the Phonon 31M weights (~65 MB) from Hugging Face; after that it works completely offline.

## Quick start

Python 3.9+ is required.

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip ...
.venv/bin/python tts.py                        # optional starting voice: tts.py male_1
```

On Linux, if `sounddevice` complains about a missing PortAudio library, install `libportaudio2` (for example `sudo apt install libportaudio2`). Windows and macOS wheels bundle PortAudio automatically.

Once it prints `Ready (...)`, type any text at the `LLM>` prompt and press Enter to hear it.
Use `/voice <name>` to switch voices without restarting, or press **Ctrl+C** to quit.

## Files

- `tts.py`: `Speaker` class and interactive CLI (update the command above if you named the file differently)
- `requirements.txt`: Python dependencies

## Using it from Python

Load `Speaker` once and reuse it so the model stays in memory.

### Full string

```python
from tts import Speaker

speaker = Speaker("female_1")
speaker.speak("Watch out, cyclist on your right.")
```

Empty or whitespace-only strings are ignored.

### LLM token stream

```python
from tts import Speaker

speaker = Speaker("male_1")
speaker.speak_stream(token_generator())
```

`speak_stream` accepts any `Iterable[str]`. It buffers incoming tokens and splits them whenever a sentence ends (`.`, `!`, `?` followed by whitespace) or on a newline. Finished sentences are pushed to a background worker thread that keeps a single audio stream open, so playback starts as soon as the first sentence is ready instead of waiting for the full reply. Any leftover text at the end is spoken before the call returns.

### Switching voices

```python
speaker.set_voice("female_2")
```

## Voices

| Alias | Voice | Notes |
|---|---|---|
| `female_1` | `Zoey` | default |
| `female_2` | `Juniper` | |
| `male_1` | `Easton` | |
| `male_2` | `Micah` | |

Aliases are case-insensitive. Any other string is passed straight through to `ptts`, so you can also pass a Phonon voice name directly (for example `Speaker("Easton")`).

## Audio in one line

Audio is synthesized by `ptts` as mono `float32` chunks at the model's native sample rate (24 kHz) and written directly to the default system output device.