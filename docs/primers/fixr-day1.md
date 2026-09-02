# FIXR Day 1 — Multimodal Evidence & Why the Evidence ID Is the Whole Trick

**Track:** FIXR — Incident & Field Support | **Phase:** 0

---

## 1. The Problem FIXR Solves

A field engineer submits a support ticket. It might arrive as:

- a **text** message ("the dashboard keeps reloading after login")
- an **audio** memo recorded on site
- a **screenshot** of the error state

The same underlying incident can land in any of these shapes. FIXR's job is to triage it — resolve, escalate, or abstain — regardless of modality, and to show its work.

"Showing its work" is not optional. A triage system that says **ESCALATE** without citing what it saw is not a system — it is a guess wearing a UI.

---

## 2. What Is Evidence?

**Evidence** is any raw artefact the system receives: a text string, an audio file, an image. Evidence is:

- **observed**, not inferred
- **immutable** — it does not change after it arrives
- **citable** — every piece of evidence gets a stable ID before any model touches it

A model's *interpretation* of the evidence (root cause, severity, disposition) is a **hypothesis**, not evidence. The two are kept strictly separate.

```
artefact (text | audio | image)
    │
    ▼
 ingestion                 ← evidence lives here
    │  eid minted
    │  raw bytes stored
    ▼
 normalised text + eid[]   ← hypothesis lives below this line
    │
    ▼
 triage model
    │
    ▼
 disposition + evidence_ids cited
```

---

## 3. Why the Evidence ID Is the Whole Trick

An **evidence ID** (`eid`) is a stable, content-addressed identifier:

```
eid = sha256(raw_bytes)[:16]
```

It is minted **once**, at ingestion, **before** any model call. It never changes.

### What the eid enables

| Without eid | With eid |
|---|---|
| "The model said ESCALATE" | "The model cited eid-a3f2 (screenshot) and eid-7b1c (log excerpt)" |
| No way to replay the decision | Replay by re-fetching the same raw artefacts |
| Prompt injection hides in the content | A dangling eid (cited but never minted) is a detectable defect |
| PII might linger undetected | Every eid has a known artefact type; image eids get a PII check |

### The dangling eid rule

A **dangling eid** is one that appears in a model response but was never minted in the current request. It means the model invented a source. This is not a hallucination to tolerate — it is a defect caught by `src/validators/provenance.py` and surfaced as a gate failure.

**One eid = one artefact. One artefact = one eid. No exceptions.**

---

## 4. The Three Input Paths

All three paths converge on a normalised text string before the triage model sees anything. The eid travels alongside it.

### Path 1 — Text

```
user message (UTF-8)
    │
    ├── eid = sha256(message.encode())[:16]
    └── normalised_text = message          → triage
```

Preprocessing: none. The message is the evidence.

### Path 2 — Audio

```
audio file (mp3 / wav / m4a)
    │
    ├── eid = sha256(audio_bytes)[:16]
    ├── transcribe via openai/whisper-large-v3-turbo (Groq free tier)
    └── normalised_text = transcript       → triage
```

The eid is on the **audio bytes**, not the transcript. If the transcript changes (different model, different language setting), the eid does not — because the evidence is what was recorded, not what the model heard.

### Path 3 — Image / Screenshot

```
screenshot (JPEG / PNG)
    │
    ├── eid = sha256(image_bytes)[:16]
    ├── PII check — STOP if unredacted PII detected
    ├── caption via NVIDIA NIM vision model
    └── normalised_text = caption          → triage
```

The eid is on the **image bytes**. The PII check runs before anything else — an unredacted screenshot never reaches the model.

---

## 5. Evidence vs Hypothesis — The Hard Boundary

```
┌─────────────────────────────┐
│         EVIDENCE            │
│  eid · raw bytes · timestamp│   ← deterministic, immutable, logged at ingestion
│  normalised text            │
└────────────┬────────────────┘
             │  (only this crosses the boundary)
             ▼
┌─────────────────────────────┐
│         HYPOTHESIS          │
│  classification             │   ← probabilistic, model-generated
│  root cause                 │
│  severity                   │
│  disposition                │
│  evidence_ids cited         │   ← must be a subset of the eids in this request
└─────────────────────────────┘
```

**Rules enforced in code:**

1. The triage prompt receives normalised text + eid list — never a prior model output.
2. The structured output schema has separate `evidence_ids: list[str]` and `hypothesis: Hypothesis` fields — they cannot be mixed.
3. `src/validators/provenance.py` rejects any response that cites an eid not in the request.
4. `src/validators/pii.py` rejects any image artefact with unredacted PII before it reaches disk.

---

## 6. Dispositions

The triage engine returns exactly one of three dispositions:

| Disposition | Meaning | When |
|---|---|---|
| `RESOLVE` | System handles it | Clear evidence, confidence above threshold, no escalation signal |
| `ESCALATE` | Hand to a human | Any mandatory-escalation signal present |
| `ABSTAIN` | System refuses to act | Evidence insufficient or contradictory |

**Mandatory-escalation signals** (any one → ESCALATE):
- Safety risk to a person
- PII / data-breach indicator
- Compliance keyword (configurable list)
- Model confidence below threshold
- Prompt-injection attempt detected

**Must-abstain signals** (any one → ABSTAIN):
- All cited eids are dangling
- Contradictory evidence, no resolvable majority
- Out-of-scope domain

---

## 7. The Cost Contract

Every model call goes through `src/telemetry.py`. Every incident ends with one printed line:

```
[METER] incident=<eid> wall_ms=<n> cost_usd=0.00 ×realtime=<n>x
```

- `cost_usd` is always `0.00` — zero spend is a hard constraint. A non-zero number means stop and ask.
- `×realtime` = wall clock / audio duration (audio only). Measures the real latency the engineer waits, not just token counts.
- Wall clock wraps the **full incident**, including network, encoding, and gaps between calls.

---

## 8. Key Vocabulary

| Term | Definition |
|---|---|
| `eid` | Content-addressed evidence ID: `sha256(raw_bytes)[:16]` |
| Evidence | Raw artefact + eid + timestamp. Observed, not inferred. |
| Hypothesis | Model output: classification, root cause, disposition, cited eids |
| Dangling eid | An eid cited in a response that was never minted — always a defect |
| `×realtime` | `wall_ms / (audio_s × 1000)` — how many times longer than the audio the system took |
| Mandatory-escalation | A signal that forces ESCALATE regardless of confidence |
| Must-abstain | A condition where ABSTAIN is the only valid disposition |

---

## 9. What to Build in Phase 0

| Task | Done when |
|---|---|
| FIXR-001 | `make doctor` passes — all credentials and tools present |
| FIXR-003 | `ARCHITECTURE.md` signed off by all four |
| FIXR-004 | 40 cases committed with provenance; dev ∩ heldout = ∅ |
| FIXR-005 | One path handles text, audio, image; every response records eids |
| FIXR-006 | Every run ends with one `[METER]` line |
| FIXR-007 | `make coach` serves this page |
| FIXR-010 | GATE: all three paths work; every response records the eids it used |
