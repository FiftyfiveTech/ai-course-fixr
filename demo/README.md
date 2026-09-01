# demo/ — inputs for `make demo` (FIXR-005)

`make demo` runs the one intake path over one of each input kind and prints the evidence records,
with the `evidence_ids` it used at the top level of the JSON. These are the inputs:

| File | Kind | Path it exercises |
|---|---|---|
| `incident_note.txt` | text | verbatim — no model call |
| *(audio)* `tests/fixtures/casual_leave_question.mp3` | audio | STT — `openai/whisper-large-v3-turbo@groq` |
| `incident_screenshot.png` | screenshot | vision — `hf.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF@ollama` |

The ticket named `nvidia/Nemotron-Nano-VL`, but NIM retired every Nemotron-Nano-VL on 2026-08-26
(410, end-of-life), so pinning it would need a paid endpoint — which zero-spend forbids. The vision
arm runs on a **local ollama VLM** instead, the same way the LLM stage keeps a local ollama arm: no
key, no network, no spend.

**Live vs offline.** The audio path calls the Groq free tier (needs `GROQ_API_KEY`); the screenshot
path calls the local ollama VLM (needs the daemon up and the model pulled — `make doctor` checks the
daemon, `ollama pull hf.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF:Q4_K_M` pulls the model). When a
path's dependency is absent its record's `content` is a labelled offline stub and `live` is `false`.
Either way the evidence **id is the same** — it is a hash of the raw input bytes, not of the model's
output — so `make demo` runs end to end even on a clean clone and is honest about which paths ran.

**The audio input** is an existing fixture (`tests/fixtures/casual_leave_question.mp3`) rather than a
file duplicated here — it is a real, decodable recording, which is what the STT path needs. It is not
an eval case and is not scored.

**The screenshot** is a placeholder dialog generated without a font library (none is installed in
the offline env), so a live read will describe a red dialog rather than a rich error. Point
`--screenshot` at a real screenshot to see the VLM read actual on-screen text:

```
uv run python -m src.triage --screenshot /path/to/real_error.png
```
