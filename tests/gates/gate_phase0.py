"""GATE Phase 0 (FIXR-010): one path, three input kinds, evidence ids recorded.

Done when this script prints a PASS table over all three input types, with $/incident.

Acceptance criteria (all must hold):
  1. text input   -> response has at least one evidence_id
  2. audio input  -> response has at least one evidence_id
  3. image input  -> response has at least one evidence_id
  4. Every evidence_id matches the schema: ev-{txt|aud|img}-<12 hex chars>
  5. No evidence_id appears in two different modality rows (no cross-contamination)
  6. wall_ms is measured and reported (cost meter running)
  7. cost_usd == 0.00 for every incident (zero-spend constraint)

The gate prints a table then asserts. A number that is only asserted and not printed is not a gate.

Usage (from repo root):
    python tests/gates/gate_phase0.py          # uses bundled fixtures
    pytest tests/gates/gate_phase0.py -v       # same, via pytest
"""
import json
import re
import sys
import time
import io
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import ingest, telemetry
from src.triage import run as triage_run

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
FIXTURES = REPO_ROOT / "tests" / "fixtures"
AUDIO_FIXTURE = FIXTURES / "casual_leave_question.mp3"

# A minimal 1×1 white PNG — avoids a large binary fixture while still exercising the image path.
# Generated once and embedded: the id is content-addressed over these exact bytes.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

TEXT_INPUT = "Dashboard keeps reloading after login. Error code 503 in console."
EID_RE = re.compile(r"^ev-(txt|aud|img)-[0-9a-f]{12}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_incident(label: str, **kwargs):
    """Run one triage incident, return (response, wall_ms, cost_usd)."""
    captured_records = []

    # Wrap incident_meter to capture the record without printing to stdout during the gate
    import src.telemetry as tel

    original_print = tel._print_meter_line

    wall_ms = None
    cost_usd = None

    def _capture(r):
        nonlocal wall_ms, cost_usd
        wall_ms = r["wall_ms"]
        cost_usd = r["cost_usd"]
        original_print(r)   # still print the METER line

    tel._print_meter_line = _capture
    try:
        incident_id = telemetry.new_turn_id()
        with telemetry.incident_meter(incident_id, label):
            response = triage_run(**kwargs, turn_id=incident_id)
    finally:
        tel._print_meter_line = original_print

    return response, wall_ms, cost_usd


def _check_eids(eids: list[str], kind_prefix: str) -> list[str]:
    """Return list of failure strings (empty = all good)."""
    failures = []
    if not eids:
        failures.append(f"evidence_ids is empty")
    for eid in eids:
        if not EID_RE.match(eid):
            failures.append(f"eid {eid!r} does not match schema ev-(txt|aud|img)-<12hex>")
        if not eid.startswith(f"ev-{kind_prefix}-"):
            failures.append(f"eid {eid!r} has wrong kind prefix for {kind_prefix} input")
    return failures


# ---------------------------------------------------------------------------
# Gate cases
# ---------------------------------------------------------------------------

CASES = []


def _case(label, kind_prefix, **run_kwargs):
    """Register one gate case."""
    CASES.append((label, kind_prefix, run_kwargs))


_case("text",  "txt", text=TEXT_INPUT)
_case("audio", "aud", audio=str(AUDIO_FIXTURE))


def _image_case():
    """Write the tiny PNG to a temp file and register the image case."""
    import tempfile
    tmp = Path(tempfile.mktemp(suffix=".png"))
    tmp.write_bytes(_TINY_PNG)
    _case("image", "img", screenshot=str(tmp))

_image_case()


# ---------------------------------------------------------------------------
# The gate runner (also the pytest entry point)
# ---------------------------------------------------------------------------

def run_gate() -> bool:
    """Run all three cases, print the table, return True if all pass."""
    rows = []
    all_eids = set()

    print("\n=== GATE Phase 0 ===\n")
    print(f"{'Input':<10} {'EIDs':<6} {'Schema':<8} {'wall_ms':>8} {'cost_usd':>10}  Status")
    print("-" * 65)

    all_ok = True

    for label, kind_prefix, run_kwargs in CASES:
        try:
            response, wall_ms, cost_usd = _run_incident(label, **run_kwargs)
        except Exception as e:
            print(f"{label:<10} {'—':<6} {'—':<8} {'—':>8} {'—':>10}  FAIL ({e})")
            all_ok = False
            continue

        eids = response.get("evidence_ids", [])
        failures = _check_eids(eids, kind_prefix)

        # cross-contamination check
        overlap = set(eids) & all_eids
        if overlap:
            failures.append(f"eid(s) appeared in a previous row: {overlap}")
        all_eids.update(eids)

        if cost_usd != 0.0:
            failures.append(f"cost_usd={cost_usd} — zero-spend constraint violated")

        status = "PASS" if not failures else f"FAIL ({'; '.join(failures)})"
        if failures:
            all_ok = False

        wall_str = f"{wall_ms:.1f}" if wall_ms is not None else "—"
        cost_str = f"{cost_usd:.2f}" if cost_usd is not None else "—"
        print(f"{label:<10} {len(eids):<6} {'ok' if not failures else 'FAIL':<8} "
              f"{wall_str:>8} {cost_str:>10}  {status}")

        rows.append({"input": label, "eids": eids, "wall_ms": wall_ms,
                     "cost_usd": cost_usd, "ok": not failures})

    print("-" * 65)
    total = len(rows)
    passed = sum(1 for r in rows if r["ok"])
    print(f"\nResult: {passed}/{total} input types PASS\n")

    return all_ok


# ---------------------------------------------------------------------------
# pytest integration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,kind_prefix,run_kwargs", CASES,
                         ids=[c[0] for c in CASES])
def test_gate_phase0(label, kind_prefix, run_kwargs):
    """Gate Phase 0: each input type produces at least one valid evidence_id."""
    response, wall_ms, cost_usd = _run_incident(label, **run_kwargs)

    eids = response.get("evidence_ids", [])
    failures = _check_eids(eids, kind_prefix)
    assert not failures, f"{label} gate failures: {failures}"
    assert cost_usd == 0.0, f"cost_usd={cost_usd} — zero-spend violated"
    assert wall_ms is not None and wall_ms > 0, "wall_ms not recorded"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ok = run_gate()
    sys.exit(0 if ok else 1)
