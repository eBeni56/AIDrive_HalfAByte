# Speech Recognition

Offline, English-only speech-to-text for the car assistant. It listens to the microphone, cleans
out road, engine, and wind noise on the fly, boosts common car controls, and strips out stutters
and tiny noise blips so the LLM gets clean sentences. Everything runs locally on the CPU (no GPU,
PyTorch, or API keys needed).

For now, it saves each finished sentence to a `.txt` file so we can test how accurate it is with
car noise before wiring it into the LLM.

## Quick start

Python 3.10+ is required. From inside the `speech-recognition` folder:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt    # macOS/Linux: .venv/bin/pip install -r requirements.txt
.venv\Scripts\python stt_driving.py                        # macOS/Linux: .venv/bin/python stt_driving.py
```

Or without a virtual environment:

```powershell
python -m pip install -r requirements.txt
python stt_driving.py
```

> **Linux note:** If `sounddevice` complains about a missing PortAudio library, install it with `sudo apt install libportaudio2`.

The first time you run it, it downloads the Moonshine English model (~245 MB) and the
DeepFilterNet3 model (~13 MB) and saves them on your machine. After that, it works completely
offline.

Just talk into the mic. You will see the text update live in the terminal while you speak, and each
finished sentence gets saved to `transcript.txt`. Press **Ctrl+C** to stop cleanly.

Handy test commands (if using `.venv`, activate it first or replace `python` with `.venv\Scripts\python`):

```powershell
python stt_driving.py -o with_keyterms.txt                      # full setup (denoiser + keyterms + cleaner)
python stt_driving.py --no-keyterms -o without_keyterms.txt     # test without car-control boosting
python stt_driving.py --no-keyterms --raw-text -o raw_stt.txt   # raw Moonshine output (no keyterms, no cleaner)
python stt_driving.py --keyterm-boost 3.0 -o boost3.txt         # stronger boost on car commands
python stt_driving.py --no-denoise -o raw_audio.txt             # test without noise removal
python stt_driving.py --list-devices                            # see which mics are available
python stt_driving.py --device 2                                # pick mic #2
```

## Files

- `stt_driving.py`: listens to the mic, cleans the audio, runs English speech-to-text with keyterms, filters out noise blips/stutters, and writes to `.txt`
- `keyterms.json`: list of basic car commands (like `"radio on"`, `"AC off"`, `"defrost"`) plus the default boost value
- `requirements.txt`: Python packages (`moonshine-voice`, `deepfilter-stream`, `sounddevice`, `numpy`)
- `transcript.txt`: created on the first run; new lines get added to the bottom (not committed)
- `README.md`: this file

## Options

| Flag | Default | What it does |
|---|---|---|
| `-o`, `--output` | `transcript.txt` | Where to save the text. Adds to the end of the file so older tests aren't wiped. |
| `--no-denoise` | off | Turns off DeepFilterNet3 and sends raw mic audio straight to Moonshine. Good for comparing results, especially if your mic or laptop already does its own noise cancellation. |
| `--atten-lim-db` | `18.0` | How hard to cut background noise, in dB (`0` means no limit). Keeping it around 12-20 dB removes engine hum without making your voice sound robotic and hurting accuracy. |
| `--keyterms-file` | `keyterms.json` | JSON file with the car-control words and phrases to boost. |
| `--no-keyterms` | off | Turns off keyterm boosting so you can compare accuracy with and without it. |
| `--keyterm-boost` | `2.0` (from config) | Overrides how strongly listed commands are boosted. `2.0` is a safe default, `3.0` pushes harder in loud noise, and `4.0+` starts hurting normal conversation. |
| `--raw-text` | off | Turns off the blip, filler, and stutter cleaner and saves raw Moonshine text as-is. |
| `--min-chars` | `3` | Drops unknown single-word blips shorter than this many letters (real short commands like `yes`, `no`, `ok`, `stop`, `go`, `mute` are still kept). |
| `--plain` | off | Saves only the spoken text, without `[HH:MM:SS]` timestamps or session headers. |
| `--device` | system default | Which microphone to use. Run `--list-devices` to see the numbers. |
| `--list-devices` | off | Lists all microphones found on the computer and exits. |
| `--save-wav-dir` | off | Saves the cleaned audio Moonshine actually hears as WAV files in this folder, so you can listen back if the text looks wrong. |

## Keyterms and text cleaning

- **Car commands (`keyterms.json`):** Tells Moonshine to pay extra attention to short car controls (climate, radio, lights, wipers, windows, navigation) so phrases like `"AC off"` or `"defrost"` don't get misheard over traffic noise. You can add or remove phrases in `keyterms.json`—just keep the list fairly short and don't put commas inside individual phrases.
- **Noise blips and stutters:** Before saving a line, `clean_transcript()` removes filler sounds (`"uh"`, `"um"`, `"hmm"`), fixes cut-off or repeated words (`"t-turn"` -> `"turn"`, `"turn on turn on the radio"` -> `"Turn on the radio"`), leaves repeated numbers alone (`"five five"`), and drops stray 1-2 letter noise blips so bumps or coughs don't trigger the LLM.

## What it writes

While you are talking, the terminal shows a live `Listening:` line that updates in place. Once you
pause, Moonshine closes the sentence and runs the cleaner: valid lines print as `Saved:` and get
written straight to the `.txt` file, while dropped noise blips print as `Ignored:` and are skipped.

Unless you pass `--plain`, each run starts with a header showing the date and active settings,
followed by one timestamped line per sentence.

### Example

```text
--- Session started 2026-11-21 15:05:12 | Denoiser: DeepFilterNet3 (atten_lim_db=18.0) | Keyterms: ON (52 terms, boost=2.0) | Cleaner: ON (min_chars=3) ---
 Turn on the AC.
 How far is the next gas station?
```

## Connecting it to the LLM

When we are ready to plug in the LLM, the spot to do it is `on_line_completed()` inside
`FileTranscriptListener` in `stt_driving.py`. That function runs the moment the driver stops
talking and already has the cleaned English `final_text` string ready to send.

## How it works in one line

`sounddevice` grabs 48 kHz mono audio in 50 ms chunks into a queue, `deepfilter-stream`
(DeepFilterNet3) strips out background noise, `moonshine-voice` (English-only, boosted with
`keyterms.json`) turns each sentence into text, and `clean_transcript()` strips fillers and
stutters before saving.