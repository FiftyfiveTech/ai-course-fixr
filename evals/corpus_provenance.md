# FIXR Corpus — Provenance & Licence

**Task:** FIXR-004 | **Owner:** Ritika | **Phase:** 0

40 cases hand-picked (no bulk download). Split: 15 dev · 25 held-out.

---

## Sources

| Source | HF Repo ID | Licence | Modality | Pool size |
|---|---|---|---|---|
| wave-ui | `agentsea/wave-ui` | unknown (check repo README before commercial use) | image + text | 79,412 (train+val+test) |
| ScreenSpot | `bevaya/ScreenSpot` | Apache-2.0 | image + text | 1,272 (test) |
| bitext-support | `bitext/Bitext-customer-support-llm-chatbot-training-dataset` | CDLA-Sharing-1.0 | text only | ~27,000 |

## Selection criteria

Cases were chosen to cover the three failure modes FIXR targets:

1. **Mandatory-escalation** — safety risk, PII exposure, compliance keyword
2. **Must-abstain** — thin or contradictory evidence
3. **Resolvable** — clear enough for `RESOLVE` disposition

No case was selected by running a model on it first (label-then-select, not select-then-label).

## Blind-labelling rule

- `evals/dev/` (15 cases) — Builder tunes on these only
- `evals/heldout/` (25 cases) — Evaluator seals Wednesday; tagged `heldout-v1`
- `tests/gates/test_no_leakage.py` asserts `dev ∩ heldout = ∅` by content hash

## Seal record — heldout-v1

| Field | Value |
|---|---|
| Sealed by | Ritika |
| Sealed date | 2026-09-02 |
| Tag | `heldout-v1` |
| Cases | 25 |
| ESCALATE count | 10 (≥8 required) |
| ABSTAIN count | 6 (≥5 required) |
| `heldout/index.jsonl` sha256 | `710a16c6a14fc6f0a34e0873e734ad211a6f8f0f71483585dfaf18315dcffd74` |
| Leakage check | dev ∩ heldout = ∅ (verified by `test_no_leakage.py`) |
| Notes spec-cited | 25/25 (every note cites §4 or §5 signal) |
