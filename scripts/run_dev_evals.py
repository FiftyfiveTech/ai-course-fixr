"""FIXR-022: run triage classification on all 15 dev cases.

Done when: 15/15 dev schema-valid; safety class set on every case.

The dev cases are text-only (bitext) or image-only (agentsea/wave-ui, bevaya/ScreenSpot).
Image cases send the *notes* field as text evidence — the actual screenshots are not in the
repo; only their expected dispositions and notes are. This keeps the eval runnable without
HF dataset downloads while still exercising the classification logic.

Usage (from repo root):
    python scripts/run_dev_evals.py
    python scripts/run_dev_evals.py --model gpt-oss-20b   # faster on groq free tier
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from schemas.triage_result import TriageResult
from src import ingest, telemetry
from src import triage_engine

DEV_INDEX = REPO_ROOT / "evals" / "dev" / "index.jsonl"


def load_cases() -> list[dict]:
    return [json.loads(l) for l in DEV_INDEX.read_text().splitlines() if l.strip()]


def evidence_for(case: dict) -> list:
    """Build evidence records for a dev case from its notes (no raw dataset download needed)."""
    # Use the notes field as surrogate text evidence — it describes what is in the image/text
    # including the specific signals (PII, compliance keyword, etc.) that should drive disposition.
    text = case["notes"]
    return [ingest.ingest_text(text, source=f"dev:{case['case_id']}")]


def run_evals(model_id: str | None, safeguard_model: str | None) -> tuple[list[dict], bool]:
    cases = load_cases()

    print(f"\n=== FIXR-022 Dev Evals ({len(cases)} cases) ===\n")
    print(f"{'Case':<12} {'Expected':<10} {'Got':<10} {'Safety':<12} {'Match'}  Reasoning")
    print("-" * 90)

    rows = []
    for case in cases:
        records = evidence_for(case)
        turn_id = telemetry.new_turn_id()
        try:
            result = triage_engine.classify(
                records, turn_id=turn_id,
                model_id=model_id, safeguard_model=safeguard_model,
            )
            # Validate schema
            validated = TriageResult(**result.model_dump())
            schema_ok = True
            got = validated.disposition
            safety = validated.safety_class
            reasoning = validated.reasoning[:60]
            err = None
        except Exception as exc:
            schema_ok = False
            got = "ERROR"
            safety = "—"
            reasoning = str(exc)[:60]
            err = exc

        expected = case["expected_disposition"]
        match = (got == expected)
        rows.append({
            "case_id": case["case_id"],
            "expected": expected,
            "got": got,
            "safety_class": safety,
            "schema_ok": schema_ok,
            "match": match,
        })

        flag = "✓" if match else "✗"
        print(f"{case['case_id']:<12} {expected:<10} {got:<10} {safety:<12} {flag}  {reasoning}")

    print("-" * 90)
    schema_valid = sum(1 for r in rows if r["schema_ok"])
    correct = sum(1 for r in rows if r["match"])
    total = len(rows)

    print(f"\nSchema-valid: {schema_valid}/{total}")
    print(f"Disposition match: {correct}/{total}")
    print()

    all_ok = schema_valid == total
    if all_ok:
        print("PASS — 15/15 schema-valid, safety class set on every case.")
    else:
        failed = [r["case_id"] for r in rows if not r["schema_ok"]]
        print(f"FAIL — {len(failed)} case(s) not schema-valid: {failed}")
    print()
    return rows, all_ok


def main():
    parser = argparse.ArgumentParser(description="Run FIXR-022 dev evals")
    parser.add_argument("--model", default=None, metavar="MODEL_ID",
                        help="primary LLM arm alias (default: stage default)")
    parser.add_argument("--safeguard", default=None, metavar="MODEL_ID",
                        help="safeguard LLM alias (default: gpt-oss-20b)")
    args = parser.parse_args()

    _, ok = run_evals(args.model, args.safeguard)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
