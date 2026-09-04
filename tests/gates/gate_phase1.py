"""GATE Phase 1 (FIXR-019): best pipeline per input type WITH NUMBERS; asks for more evidence
rather than guessing on thin inputs.

Done when this script prints both tables and every assertion passes.

Acceptance criteria (all must hold):
  1. text   input -> ≥1 evidence_id, schema valid, cost_usd == 0.00
  2. log    input -> one evidence_id per non-blank line, all schema valid, cost_usd == 0.00
  3. image  input -> ≥1 evidence_id, schema valid, cost_usd == 0.00
  4. audio  input -> ≥1 evidence_id, schema valid, cost_usd == 0.00
  5. is_thin() correctly identifies thin inputs as thin (≥ THIN_FLOOR of thin cases)
  6. is_thin() correctly rejects specific inputs (≥ SPECIFIC_FLOOR of specific cases)
  7. Log parser: EID count == non-blank line count for every log sample

The gate is runnable without API keys: text and log paths need no model; audio and image fall
back to offline stubs when credentials / the ollama daemon are absent, but still produce a
valid evidence_id.

Usage (from repo root):
    python tests/gates/gate_phase1.py
    pytest  tests/gates/gate_phase1.py -v
"""
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import ingest, log_parser, telemetry
from src.clarify import is_thin
from src.triage import run as triage_run

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

THIN_FLOOR = 0.80     # at least 80 % of thin cases detected as thin
SPECIFIC_FLOOR = 0.80  # at least 80 % of specific cases correctly NOT flagged as thin

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

EID_RE = re.compile(r"^ev-(txt|aud|img)-[0-9a-f]{12}$")

# A minimal 1×1 white PNG — same bytes as gate_phase0 so the id is reproducible.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

FIXTURES = REPO_ROOT / "tests" / "fixtures"
AUDIO_FIXTURE = FIXTURES / "casual_leave_question.mp3"

# A real-size screenshot fixture — used for the image input case so the test works
# whether the vision arm is online (VLM rejects 1×1 images) or offline (stub).
IMAGE_FIXTURE = FIXTURES / "ocr_cases" / "error_dialog.png"

TEXT_INPUT   = "Dashboard keeps reloading after login. Error code 503 in console."
LOG_SAMPLE   = (
    "2024-01-15T09:12:34.567 ERROR [prod-3] OSError: No space left on device\n"
    "2024-01-15T09:12:35.100 WARN  [prod-3] Retry 1/3 failed — sleeping 5 s\n"
    "\n"                                    # blank line — must be skipped
    "2024-01-15T09:12:40.002 INFO  [prod-3] df: /dev/sda1 100% (disk full)\n"
    "Jan 15 09:12:41 prod-3 kernel: EXT4-fs error (device sda1): ext4_journal_check_start\n"
)
LOG_NONBLANK_LINES = 4                     # blank line excluded


def _check_eids(eids: list[str], kind_prefix: str) -> list[str]:
    """Return failure strings (empty = all good)."""
    failures = []
    if not eids:
        failures.append("evidence_ids is empty")
    for eid in eids:
        if not EID_RE.match(eid):
            failures.append(f"eid {eid!r} does not match schema ev-(txt|aud|img)-<12hex>")
        if not eid.startswith(f"ev-{kind_prefix}-"):
            failures.append(f"eid {eid!r} has wrong kind prefix for {kind_prefix}")
    return failures


def _run_incident(label: str, **kwargs):
    """Run one triage incident; return (response, wall_ms, cost_usd)."""
    import src.telemetry as tel
    original_print = tel._print_meter_line
    wall_ms = cost_usd = None

    def _capture(r):
        nonlocal wall_ms, cost_usd
        wall_ms, cost_usd = r["wall_ms"], r["cost_usd"]
        original_print(r)

    tel._print_meter_line = _capture
    try:
        incident_id = telemetry.new_turn_id()
        with telemetry.incident_meter(incident_id, label):
            response = triage_run(**kwargs, turn_id=incident_id)
    finally:
        tel._print_meter_line = original_print

    return response, wall_ms, cost_usd


# ---------------------------------------------------------------------------
# Section 1: per-input-type coverage
# ---------------------------------------------------------------------------

INPUT_CASES = []   # (label, kind_prefix, triage_run_kwargs)


def _register(label, kind_prefix, **kw):
    INPUT_CASES.append((label, kind_prefix, kw))


def _setup_input_cases():
    _register("text",  "txt", text=TEXT_INPUT)
    _register("log",   "txt", log=LOG_SAMPLE, log_source="gate_phase1:log")

    # Use a real-size fixture when available; fall back to the tiny PNG so the gate
    # still runs on a clean clone before the fixture directory is populated.
    img_path = IMAGE_FIXTURE if IMAGE_FIXTURE.exists() else Path(tempfile.mktemp(suffix=".png"))
    if not img_path.exists():
        img_path.write_bytes(_TINY_PNG)
    _register("image", "img", screenshot=str(img_path))

    if AUDIO_FIXTURE.exists():
        _register("audio", "aud", audio=str(AUDIO_FIXTURE))


_setup_input_cases()


def run_input_table() -> tuple[list[dict], bool]:
    """Run the per-input-type table; return (rows, all_ok)."""
    print("\n--- Per-Input-Type Pipeline ---")
    print(f"{'Input':<8} {'EIDs':>5} {'Schema':<8} {'wall_ms':>8} {'cost_usd':>10}  Status")
    print("-" * 58)

    rows = []
    all_ok = True

    for label, kind_prefix, kw in INPUT_CASES:
        try:
            resp, wall_ms, cost_usd = _run_incident(label, **kw)
        except Exception as exc:
            print(f"{label:<8} {'—':>5} {'—':<8} {'—':>8} {'—':>10}  FAIL ({exc})")
            all_ok = False
            continue

        eids = resp.get("evidence_ids", [])
        failures = _check_eids(eids, kind_prefix)
        if cost_usd != 0.0:
            failures.append(f"cost_usd={cost_usd}")

        # Log inputs produce one EID per non-blank line
        if label == "log" and len(eids) != LOG_NONBLANK_LINES:
            failures.append(
                f"log: expected {LOG_NONBLANK_LINES} EIDs (one per non-blank line), got {len(eids)}"
            )

        ok = not failures
        if not ok:
            all_ok = False
        status = "PASS" if ok else f"FAIL ({'; '.join(failures)})"
        wall_str = f"{wall_ms:.1f}" if wall_ms is not None else "—"
        cost_str = f"{cost_usd:.2f}" if cost_usd is not None else "—"
        print(f"{label:<8} {len(eids):>5} {'ok' if ok else 'FAIL':<8} "
              f"{wall_str:>8} {cost_str:>10}  {status}")
        rows.append({"label": label, "eids": eids, "wall_ms": wall_ms,
                     "cost_usd": cost_usd, "ok": ok})

    print("-" * 58)
    passed = sum(1 for r in rows if r["ok"])
    print(f"Input coverage: {passed}/{len(rows)} PASS\n")
    return rows, all_ok


# ---------------------------------------------------------------------------
# Section 2: thin-evidence detection (is_thin accuracy)
# ---------------------------------------------------------------------------

# Inputs that ARE thin (ambiguous / too vague to diagnose)
THIN_CASES = [
    ("help",                        True),
    ("something is wrong",          True),
    ("prod is down",                True),
    ("getting errors",              True),
    ("network issue",               True),
]

# Inputs that are NOT thin (have enough specificity to attempt diagnosis)
SPECIFIC_CASES = [
    ("OSError: No space left on /var/log",          False),
    ("HTTP 500 on /api/checkout since 09:12",       False),
    ("exit code 0xc000007b on prod-3.example.com",  False),
    ("df shows /dev/sda1 at 100%",                  False),
    ("2024-01-15T09:12:34 ERROR db connection pool exhausted", False),
]


def run_thin_table() -> tuple[int, int, bool]:
    """Run the thin-evidence detection table; return (correct, total, meets_threshold)."""
    all_cases = THIN_CASES + SPECIFIC_CASES

    print("--- Thin-Evidence Detection (is_thin) ---")
    print(f"{'Content':<52} {'Expected':<10} {'Got':<10} {'Match'}")
    print("-" * 80)

    correct = 0
    for content, expected_thin in all_cases:
        from schemas.evidence import EvidenceRecord
        rec = EvidenceRecord.build(
            kind="text", raw=content.encode("utf-8"), content=content,
            source="gate", origin="test", live=True,
        )
        got = is_thin([rec])
        match = (got == expected_thin)
        if match:
            correct += 1
        label = "thin" if expected_thin else "specific"
        got_str = "thin" if got else "specific"
        print(f"{content[:50]:<52} {label:<10} {got_str:<10} {'✓' if match else '✗'}")

    print("-" * 80)
    total = len(all_cases)
    thin_total = len(THIN_CASES)
    specific_total = len(SPECIFIC_CASES)

    # Score thin and specific separately
    thin_correct    = sum(1 for c, e in THIN_CASES
                          if is_thin([EvidenceRecord.build(
                              kind="text", raw=c.encode(), content=c,
                              source="gate", origin="test", live=True)])  == e)
    specific_correct = sum(1 for c, e in SPECIFIC_CASES
                           if is_thin([EvidenceRecord.build(
                               kind="text", raw=c.encode(), content=c,
                               source="gate", origin="test", live=True)]) == e)

    thin_rate     = thin_correct / thin_total if thin_total else 0.0
    specific_rate = specific_correct / specific_total if specific_total else 0.0

    print(f"Thin inputs:     {thin_correct}/{thin_total}  ({thin_rate:.0%})   "
          f"[threshold ≥{THIN_FLOOR:.0%}]  {'PASS' if thin_rate >= THIN_FLOOR else 'FAIL'}")
    print(f"Specific inputs: {specific_correct}/{specific_total}  ({specific_rate:.0%})  "
          f"[threshold ≥{SPECIFIC_FLOOR:.0%}]  {'PASS' if specific_rate >= SPECIFIC_FLOOR else 'FAIL'}")
    print()

    meets = (thin_rate >= THIN_FLOOR) and (specific_rate >= SPECIFIC_FLOOR)
    return correct, total, meets


# ---------------------------------------------------------------------------
# Section 3: log parser — lines → EIDs
# ---------------------------------------------------------------------------

LOG_SAMPLES = [
    ("3-line ISO log",       "line A\nline B\nline C\n",                                  3),
    ("blank lines skipped",  "line A\n\nline B\n\n\nline C\n",                            3),
    ("structured fields",    LOG_SAMPLE,                                                  LOG_NONBLANK_LINES),
    ("single line",          "2024-01-15T09:12:34 ERROR [svc] connection refused\n",     1),
]


def run_log_table() -> tuple[list[dict], bool]:
    """Run the log parser table; return (rows, all_ok)."""
    print("--- Log Parser: lines → evidence ids ---")
    print(f"{'Sample':<22} {'Lines':>6} {'EIDs':>6} {'Match':>6}  {'Schema':<6}  Status")
    print("-" * 60)

    rows = []
    all_ok = True

    for name, text, expected_n in LOG_SAMPLES:
        records = log_parser.ingest_log(text)
        eids = [r.id for r in records]
        schema_ok = all(EID_RE.match(e) for e in eids)
        count_ok  = len(eids) == expected_n
        ok = schema_ok and count_ok
        if not ok:
            all_ok = False
        status = "PASS" if ok else f"FAIL (got {len(eids)}, expected {expected_n})"
        print(f"{name:<22} {expected_n:>6} {len(eids):>6} {'ok' if count_ok else 'FAIL':>6}  "
              f"{'ok' if schema_ok else 'FAIL':<6}  {status}")
        rows.append({"name": name, "expected": expected_n, "got": len(eids), "ok": ok})

    print("-" * 60)
    passed = sum(1 for r in rows if r["ok"])
    print(f"Log parser: {passed}/{len(rows)} PASS\n")
    return rows, all_ok


# ---------------------------------------------------------------------------
# Full gate runner
# ---------------------------------------------------------------------------

def run_gate() -> bool:
    print("\n=== GATE Phase 1 ===\n")

    _, input_ok   = run_input_table()
    _, _, thin_ok = run_thin_table()
    _, log_ok     = run_log_table()

    all_ok = input_ok and thin_ok and log_ok
    total  = 3
    passed = sum([input_ok, thin_ok, log_ok])
    print(f"Result: {passed}/{total} sections PASS")
    if not all_ok:
        print("GATE PHASE 1 FAILED — fix issues above before advancing.")
    else:
        print("GATE PHASE 1 PASSED.")
    print()
    return all_ok


# ---------------------------------------------------------------------------
# pytest integration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,kind_prefix,kw", INPUT_CASES,
                         ids=[c[0] for c in INPUT_CASES])
def test_gate_phase1_input_coverage(label, kind_prefix, kw):
    """Criterion 1–4: each input type produces ≥1 valid EID, cost_usd 0."""
    resp, wall_ms, cost_usd = _run_incident(label, **kw)
    eids = resp.get("evidence_ids", [])
    failures = _check_eids(eids, kind_prefix)
    assert not failures, f"{label}: {failures}"
    assert cost_usd == 0.0, f"cost_usd={cost_usd}"
    assert wall_ms is not None and wall_ms >= 0


def test_gate_phase1_log_eids_per_line():
    """Criterion 7: log parser produces exactly one EID per non-blank line."""
    records = log_parser.ingest_log(LOG_SAMPLE)
    assert len(records) == LOG_NONBLANK_LINES, (
        f"expected {LOG_NONBLANK_LINES} records, got {len(records)}"
    )
    for r in records:
        assert EID_RE.match(r.id), f"bad EID schema: {r.id}"


def test_gate_phase1_thin_detection_rate():
    """Criterion 5–6: is_thin() meets THIN_FLOOR on thin inputs and SPECIFIC_FLOOR on specific."""
    from schemas.evidence import EvidenceRecord

    def rec(content):
        return EvidenceRecord.build(kind="text", raw=content.encode(), content=content,
                                    source="gate", origin="test", live=True)

    thin_correct = sum(1 for c, e in THIN_CASES if is_thin([rec(c)]) == e)
    specific_correct = sum(1 for c, e in SPECIFIC_CASES if is_thin([rec(c)]) == e)

    thin_rate     = thin_correct / len(THIN_CASES)
    specific_rate = specific_correct / len(SPECIFIC_CASES)

    assert thin_rate >= THIN_FLOOR, (
        f"thin detection rate {thin_rate:.0%} < {THIN_FLOOR:.0%} — "
        f"{thin_correct}/{len(THIN_CASES)} thin cases correctly identified"
    )
    assert specific_rate >= SPECIFIC_FLOOR, (
        f"specific rejection rate {specific_rate:.0%} < {SPECIFIC_FLOOR:.0%} — "
        f"{specific_correct}/{len(SPECIFIC_CASES)} specific cases correctly NOT flagged"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ok = run_gate()
    sys.exit(0 if ok else 1)
