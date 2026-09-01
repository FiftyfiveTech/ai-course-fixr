# FIXR — System Architecture

**Status:** Draft · FIXR-003 · Owners: Ritika + Vimal · Phase 0

> No code is written until all four team members sign off on this document.

---

## Overview

FIXR is an AI-assisted incident and field-support triage system. It ingests multi-modal evidence
(text, audio, image), mints an evidence ID for every artefact, classifies the incident, and
decides whether to resolve, escalate, or abstain.

```
                  ┌─────────────┐
   text ──────────▶             │
   audio ─────────▶  Ingestion  │──▶ evidence_id[]  ──▶  Triage  ──▶  Response
   image ─────────▶             │                         Engine
                  └─────────────┘
```

---

## 1. Three Input Paths

### 1.1 Text

- Source: free-form support messages, chat transcripts, log snippets
- Format: UTF-8 string
- Preprocessing: none (passed as-is to the triage prompt)
- Module: `src/ingest/text.py`

### 1.2 Audio

- Source: voice memos, call recordings
- Format: any format supported by `openai/whisper-large-v3-turbo` (mp3, wav, m4a, …)
- Provider: Groq free-tier (`whisper-large-v3-turbo`)
- Preprocessing: transcribed to text → then follows the text path
- Module: `src/ingest/audio.py`

### 1.3 Image / Screenshot

- Source: screenshots, photos of error states, UI captures
- Format: JPEG / PNG
- Provider: NVIDIA NIM free-tier vision model (to be confirmed during Phase 0)
- Preprocessing: described/captioned by vision model → then follows the text path
- Module: `src/ingest/image.py`

All three paths converge on a single normalised text string before triage.

---

## 2. Where Evidence IDs Are Minted

An **evidence ID** (`eid`) is a stable, opaque identifier assigned to every raw artefact the
system receives. It is the unit of provenance.

**Rule:** one artefact → one `eid`. An `eid` is minted at ingestion time, before any model call.

```
artefact (text | audio | image)
    │
    ▼
src/ingest/<type>.py
    │  eid = sha256(content)[:16]          # deterministic, content-addressed
    │  record(eid, type, raw_bytes, ts)     # written to evidence store
    ▼
normalised_text  +  eid[]
```

Properties:
- **Deterministic:** same bytes → same `eid` (deduplication is free)
- **Immutable:** the `eid` never changes once minted; the raw artefact is kept as-is
- **Logged:** every minting event goes through `src/telemetry.py` (cost/latency logger)

A response that cites an `eid` that was never minted in this session is a **dangling reference** —
treated as a defect by `tests/gates/` and caught by `src/validators/provenance.py`.

---

## 3. Evidence / Hypothesis Boundary

The system enforces a hard separation between **evidence** (what was observed) and **hypothesis**
(what the model infers).

| Layer | Contains | Who writes it |
|---|---|---|
| Evidence | Raw artefact + `eid` + timestamp | Ingestion modules (deterministic) |
| Hypothesis | Classification, root-cause, severity | Triage LLM (probabilistic) |

**Enforcement rules:**

1. The triage prompt receives only normalised text + `eid` list — never the model's prior output.
2. The structured output schema (`schemas/triage_output.py`) has separate `evidence_ids: list[str]`
   and `hypothesis: Hypothesis` fields; they cannot be mixed.
3. A response may only cite `eid`s that appear in the current request's evidence payload.
   `src/validators/provenance.py` checks this before the response is returned.
4. PII check runs on every image artefact before it touches disk
   (`src/validators/pii.py`); unredacted screenshots are a gate failure.

---

## 4. What Triggers Escalation

The triage engine outputs one of three dispositions:

| Disposition | Meaning | Condition |
|---|---|---|
| `RESOLVE` | System can handle it | Confidence ≥ threshold AND no mandatory-escalation signal |
| `ESCALATE` | Hand to a human | Any mandatory-escalation signal present (see below) |
| `ABSTAIN` | System refuses to act | Evidence is insufficient or contradictory |

**Mandatory-escalation signals** (any one triggers `ESCALATE`):

- Safety risk to a person detected in the evidence
- Data-breach or PII-exposure indicator
- Regulatory / compliance keyword (configurable list in `prompts/escalation_keywords.txt`)
- Model confidence below `ESCALATION_CONFIDENCE_FLOOR` (env var, default `0.6`)
- Prompt-injection attempt detected by `Llama-Prompt-Guard-2-86M` (FIXR-023)

**Must-abstain signals** (any one triggers `ABSTAIN`):

- All `eid`s in the response are dangling (no grounding)
- Contradictory evidence with no resolvable majority
- Out-of-scope domain (model signals `domain: unknown`)

The escalation logic lives in `src/triage/escalation.py` and is tested against
`evals/dev/` cases before any gate run.

---

## 5. Module Map (target state after Phase 0)

```
src/
  ingest/
    text.py          # path 1 — normalise text input, mint eid
    audio.py         # path 2 — Whisper transcription → text path
    image.py         # path 3 — vision caption → text path
  triage/
    engine.py        # assembles prompt, calls LLM, parses structured output
    escalation.py    # applies escalation / abstain rules
  validators/
    provenance.py    # dangling eid check
    pii.py           # unredacted screenshot check
  telemetry.py       # cost/latency logger (every model call goes through here)

schemas/
  triage_output.py   # Pydantic: evidence_ids, hypothesis, disposition, confidence

prompts/
  triage_v1.md       # versioned triage prompt
  escalation_keywords.txt

tests/gates/
  test_no_leakage.py # dev ∩ heldout = ∅ by content hash (FIXR-015)
  test_gate_phase0.py # GATE Phase 0: all three paths, evidence ids recorded

evals/
  dev/               # 15 cases — Builder tunes here only
  heldout/           # 25 cases — Evaluator only, sealed heldout-v1
```

---

## 6. Cost & Latency Contract

Every model call must be wrapped by `src/telemetry.py`, which records:

- `eid` (or `None` for non-artefact calls)
- model HF repo id
- provider (groq / nvidia-nim / ollama)
- input tokens, output tokens, wall-clock latency
- estimated cost (zero for free-tier; flagged if non-zero)

Gate metrics reported per incident: `$/incident` and `×realtime` (wall-clock vs audio duration).

---

## Sign-off

| Name | Role | Signed |
|---|---|---|
| Ritika | Builder | ☐ |
| Vimal | Builder | ☐ |
| Supervisor 1 | Evaluator | ☐ |
| Supervisor 2 | Evaluator | ☐ |

> All four must sign before any Phase 0 code is written.
