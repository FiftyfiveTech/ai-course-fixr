# ai-course-fixr — ROLE (Track 4)

Interactive AI roleplay & skills coach. One repo per track, grown phase by phase by hand.
See `docs/2026-07-28-track4-role-roleplay-coach.md` for the PRD.

## Initial infra (ported from VOX)

Rather than rebuild the plumbing, ROLE starts from `ai-course-vox`'s proven, zero-spend infra
spine — the model interface, cost/latency logging, free-tier fallback, the voice layer, and hybrid
retrieval. It is copied in and runs offline out of the box:

```bash
make setup                                   # uv sync + write .env
uv run pytest tests --ignore=tests/gates -q  # 411 passed in 14.43s — no network, no key, no mic
```

What was copied, what runs, and the **keep / adapt / replace** map for each module as ROLE is
built: `docs/vox-reuse-port-plan.md`. The short version — `config/telemetry/errors/cooldown/arms/
nlu`, the voice layer, and `sources/retrieval/embeddings` are **keep**; `answer/state/history/
scorer/confirm` are **adapt**; `loop/harness` and the HR-domain `dates/figures` are **replace or
prune**. The persona agent, scenario controller, observer/evaluator, and evidence-linked
scorecards are net-new — VOX has no equivalent.

## Layout

| Path | Holds |
|---|---|
| `src/` | The system. Small modules, one job each. Currently the VOX infra spine (`arms.py` is the one model interface; `telemetry/errors/cooldown`, the `vad/stt/tts/audio` voice layer, and `sources/retrieval/embeddings` retrieval). |
| `prompts/` | Versioned prompt files (`extract_v1.md`, `extract_v2.md`, …). Never inline a prompt in code. |
| `schemas/` | Pydantic models. Structured output is validated, not parsed by hand. |
| `config.yaml` | VAD / barge-in thresholds (the tunables a script measures, kept out of the env). |
| `evals/dev/` | **Builder** tunes here. 15 cases. |
| `evals/heldout/` | **Evaluator** only. Sealed Wednesday, tagged `heldout-v1`. The Builder never reads it. |
| `tests/unit/` | The failure modes as tests — the ported spine's offline suite. `make test`. |
| `tests/gates/` | One script per phase gate. It prints the number; the number decides. |
| `docs/` | The PRD and `vox-reuse-port-plan.md` (what was ported and the keep/adapt/replace map). |
| `STANDUP.md` | Daily log. Two minutes, append-only. |

## Still missing

- `tests/gates/test_no_leakage.py` — asserts `evals/dev ∩ evals/heldout = ∅` by content hash
  (task **0.7**). Until it exists, the blind-labelling rule is unenforced. Write it; do not import
  it from somewhere else.

`src/telemetry.py` (task 0.8) is no longer missing — it was ported in with the VOX infra spine and
is the logger every model call already goes through. Keep it as-is.

## Rules that live in this repo

`CLAUDE.md` carries the full contract. The short version:

- Models and datasets are named by **Hugging Face repo id**. The provider is only where it runs.
- **Zero spend.** A paid call is a STOP-and-ask, never a judgement call.
- A phase is done when its gate **prints the number**, not when the code looks right.
- Every PR is reviewed by the other person. `main` is protected; self-merges are the one thing
  the Friday retro always checks.
- Tasks come from the Odoo board via the `odoo-board` MCP server, not from this README.
