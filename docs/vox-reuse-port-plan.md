# VOX → ROLE infra port

The ROLE track (Track 4, roleplay & skills coach) reuses VOX's proven, zero-spend infra
spine as its starting baseline. This doc records **what was copied**, **that it runs**, and
**which modules to keep, adapt, or replace** as ROLE is built.

## What was copied (from `ai-course-vox`)

- `src/*.py` — all 24 modules
- `schemas/turn_state.py`
- `prompts/*.md` — 15 files
- `config.yaml`, `.python-version` (3.12)
- `tests/unit/*` (minus two driver tests — see below), `tests/test_scorer.py`, `tests/fixtures/*.mp3`
- `pyproject.toml` dependency list (torch, kokoro, silero-vad, faster-whisper, transformers,
  rank-bm25, piper, en-core-web-sm, …)

**Not copied:** `scripts/` (VOX driver scripts), VOX's mic entry points as final code, `sources/`
(internal HR PDFs), `runs/`, VOX docs/notes. `test_compare.py` and `test_rehearsal.py` were
dropped because they `importlib`-load `scripts/compare_arms.py` / `scripts/dry_run.py` — VOX
demo/benchmark drivers that are not part of the reusable spine.

## It runs

```
uv sync --directory .
uv run pytest tests --ignore=tests/gates -q
# 411 passed in 14.43s   (offline: no network, no key, no mic)
```

## Keep / adapt / replace — per module

| Module(s) | Verdict | Note for ROLE |
|---|---|---|
| `config.py` `telemetry.py` `errors.py` `cooldown.py` `arms.py` `nlu.py` `vocab_bias.py` | **Keep** | Model interface, cost/latency logging, free-tier fallback + cooldown. The zero-spend contract, intact. |
| `vad.py` `stt.py` `tts.py` `audio.py` | **Keep** | The optional-voice layer (Phase 0 "optional STT/TTS"). |
| `sources.py` `retrieval.py` `embeddings.py` | **Keep** | Hybrid BM25+dense retrieval with provenance (Phase 2 grounding of persona + evaluator). |
| `answer.py` | **Adapt** | Grounded-or-refuse is reusable; it currently pulls VOX-domain `dates`/`figures` guards. Decouple those for ROLE, or repurpose as the evaluator's factual-claim checker. |
| `state.py` + `schemas/turn_state.py` | **Adapt** | Structured-output + validate-or-raise pattern → reuse for scorecard schemas (new Pydantic models). |
| `history.py` | **Adapt** | 3-turn retrieval-query helper → grow into full session/transcript state for multi-turn roleplay. |
| `scorer.py` | **Adapt** | Precision/recall-vs-gold + agreement harness → template for Phase 1B rubric scoring. **Net-new:** evidence-linking (every score cites a transcript segment). |
| `confirm.py` | **Adapt** | Confirmation flow → Phase 3 tool gating + "block unapproved disclosure" guard. |
| `loop.py` `harness.py` | **Replace** | VOX single-turn command loop. ROLE builds its own persona-driven session on top of the kept infra. |
| `dates.py` `figures.py` + `compute_*`/`leave`/`escalate` prompts | **Prune** | VOX HR-domain logic (leave, pay). Not needed by ROLE; kept only so the copied suite stays green until pruned. |

## Net-new for ROLE (no VOX equivalent)

Persona agent · scenario controller (state machine + difficulty) · observer + evaluator agents ·
**evidence-linked scorecards** · learning-plan generation · trainer review/override · multi-agent
orchestration · consent/retention machinery · FastAPI service layer.
