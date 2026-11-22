# safety_ai

The car's voice and its LLM driving coach. One Python program on this PC (it needs the speakers, which Docker
can't reach). It connects to [central_point](../central_point/README.md) and

* **speaks every warning at once**: central_point decides them (eyes closed, phone, looking away, a road user the
  driver did not look at in time) and sends the line to say, only during a run (GO to the end), so nothing
  interrupts the end-of-drive analysis. A more urgent line interrupts a less urgent one; a
  warning that could not be spoken within 2.5 s is dropped (an old warning is worse than none);
* **writes and speaks the coaching**: when central_point sees a pattern (for example two cars on the right missed)
  it sends a `coach_request` with what happened; the LLM finds the point, writes a short tip and it is spoken. At
  the end of a run central_point sends the full statistics of the run (misses with where the driver looked,
  phone / eyes closed / looking down times, mirror checks, warnings, previous runs) and the LLM, as a driving
  instructor, writes the end-of-drive analysis: a verdict, what went wrong with the numbers, how to fix it, what
  went well and the focus for the next drive (~100 words spoken, ~35 s). Both are sent back (`coach_result`) and
  appear in the dashboard's chat. A tip is not spoken when a warning came in meanwhile.

| File | What |
|---|---|
| `main.py` | the connection to central_point (reconnects every 2 s), warnings to the voice, coach requests to the LLM |
| `voice.py` | text-to-speech: Phonon (`ptts`), offline on the CPU; priorities, interruptions, a cache of spoken lines |
| `coach_llm.py` | the LLM: Azure OpenAI `gpt-4.1-mini` (Responses API), the coach prompts, strict JSON answers |
| `.env` | the Azure endpoint, key and deployment: **not in git**, never share it |

## Setup

`start.bat` / `start.sh` in the repository root set it up on the first run (`safety_ai\.venv`, Python 3.12, with
uv if installed) and start it in its own minimised window titled "safety ai". The Phonon voice (~65 MB) is
downloaded from Hugging Face on the first start; afterwards it works offline. The LLM needs the internet.

`.env`: copy `.env.example` to `.env` and fill it in (the key is on the deployment's page in Microsoft Foundry):

```
AZURE_OPENAI_ENDPOINT=https://<resource>.services.ai.azure.com/openai/v1/responses
AZURE_OPENAI_KEY=<key>
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
```

Only the host of the endpoint is used (requests go to `/openai/v1/responses`; the project URL Foundry shows
refuses the key). Without `.env` or without internet the warnings are still spoken; there are just no tips.

Check the connection: at start safety_ai prints `LLM coach ready (gpt-4.1-mini)` or `NOT reachable`.

## Run by hand

```
.venv\Scripts\python main.py                 # Linux / macOS: .venv/bin/python main.py
.venv\Scripts\python main.py --mute          # print the lines instead of speaking
.venv\Scripts\python main.py --no-llm        # warnings only
.venv\Scripts\python main.py --voice male_1  # female_1 (default), female_2, male_1, male_2
.venv\Scripts\python main.py --hub ws://192.168.1.20:8765
```

`start.bat` passes `SAI_ARGS` (e.g. `set SAI_ARGS=--mute`); `NO_SAFETY_AI=1` does not start it.

## Measured on the team laptop (i7-1355U, no GPU)

| | |
|---|---|
| Azure `gpt-4.1-mini`, tip / end-of-drive analysis | 0.7-1.4 s / 1.6-4 s |
| Phonon: model load / first audio of a new line / a cached line | ~1 s / 110-150 ms / at once |
| A warning interrupting a tip | ~50 ms |
| Tokens of an analysis | ~900-1,500 in, ~200-400 out |
| Cost | ~$0.0003 per tip, ~$0.001 per analysis |

gpt-5-mini was tested for the analysis too: not better on these reports, and 5-35 s instead of 2-4 s. Tips and
analysis both use `AZURE_OPENAI_DEPLOYMENT` (gpt-4.1-mini).
