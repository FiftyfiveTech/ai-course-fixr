"""FIXR-015: dev ∩ heldout = ∅ by content hash.

Done when:
  - The test FAILS when a held-out case is planted in dev (not a silent green).
  - A vacuous PASS (empty sets on either side) is reported explicitly — a corpus
    with zero cases is not a passing corpus.
  - The intersection is checked by content hash, not case_id, so a copy-paste
    with a renamed id is still caught.

Content hash: sha256 of the canonical JSON representation of the identifying
fields (source, split, row_index). These three fields uniquely identify a row
in its upstream HF dataset, so two records with the same triple are the same
underlying case regardless of what case_id or notes say.

Run:
    PYTHONPATH=. .venv/bin/python -m pytest tests/gates/test_no_leakage.py -v
"""
import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEV_INDEX = REPO_ROOT / "evals" / "dev" / "index.jsonl"
HELDOUT_INDEX = REPO_ROOT / "evals" / "heldout" / "index.jsonl"

# Minimum corpus sizes — a vacuous PASS on an empty file is a silent failure.
MIN_DEV = 1
MIN_HELDOUT = 1


def _content_hash(record: dict) -> str:
    """Stable fingerprint for a case based on its upstream identity.

    Uses source + split + row_index — the three fields that locate a row in its
    HF dataset. Sorting the keys makes the hash independent of JSON field order.
    """
    identity = {k: record[k] for k in ("source", "split", "row_index")}
    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dev_index_exists_and_nonempty():
    """Dev index must exist and have at least one case."""
    records = _load(DEV_INDEX)
    assert DEV_INDEX.exists(), f"missing: {DEV_INDEX}"
    assert len(records) >= MIN_DEV, (
        f"dev index has {len(records)} cases — need at least {MIN_DEV}. "
        f"A vacuous PASS on an empty corpus is not a PASS."
    )


def test_heldout_index_exists_and_nonempty():
    """Heldout index must exist and have at least one case."""
    records = _load(HELDOUT_INDEX)
    assert HELDOUT_INDEX.exists(), f"missing: {HELDOUT_INDEX}"
    assert len(records) >= MIN_HELDOUT, (
        f"heldout index has {len(records)} cases — need at least {MIN_HELDOUT}. "
        f"A vacuous PASS on an empty corpus is not a PASS."
    )


def test_no_leakage():
    """dev ∩ heldout = ∅ by content hash (source + split + row_index)."""
    dev_records = _load(DEV_INDEX)
    heldout_records = _load(HELDOUT_INDEX)

    # Vacuous-PASS guard: if either set is empty the intersection is trivially
    # empty — but that proves nothing and must not be reported as a passing gate.
    assert len(dev_records) >= MIN_DEV, (
        f"dev has {len(dev_records)} cases — refusing to call ∅ ∩ anything a PASS."
    )
    assert len(heldout_records) >= MIN_HELDOUT, (
        f"heldout has {len(heldout_records)} cases — refusing to call ∅ ∩ anything a PASS."
    )

    dev_hashes = {_content_hash(r): r["case_id"] for r in dev_records}
    heldout_hashes = {_content_hash(r): r["case_id"] for r in heldout_records}

    overlap = set(dev_hashes) & set(heldout_hashes)

    if overlap:
        leaks = [
            f"  dev:{dev_hashes[h]} == heldout:{heldout_hashes[h]}"
            for h in sorted(overlap)
        ]
        pytest.fail(
            f"LEAKAGE DETECTED — {len(overlap)} case(s) appear in both dev and heldout:\n"
            + "\n".join(leaks)
        )

    # Print the counts so the result is visible, not just asserted.
    print(
        f"\n[no-leakage] dev={len(dev_records)} heldout={len(heldout_records)} "
        f"intersection={len(overlap)}  PASS"
    )


# ---------------------------------------------------------------------------
# Self-test: the test MUST fail when a held-out case is planted in dev
# ---------------------------------------------------------------------------

def test_leakage_is_detected_when_planted(tmp_path):
    """Regression guard: verify that planting a heldout case in dev triggers a failure.

    If this test passes, the leakage check is actually checking something.
    If this test fails, the leakage check is broken and would give a false green.
    """
    heldout_records = _load(HELDOUT_INDEX)
    if not heldout_records:
        pytest.skip("heldout index is empty — nothing to plant")

    # Write a dev index that contains the first heldout case verbatim.
    planted = heldout_records[0].copy()
    planted["case_id"] = "dev-planted"   # rename id — hash must still catch it

    fake_dev = tmp_path / "dev_index.jsonl"
    fake_dev.write_text(json.dumps(planted) + "\n")

    fake_heldout = tmp_path / "heldout_index.jsonl"
    fake_heldout.write_text("\n".join(json.dumps(r) for r in heldout_records))

    dev_records = _load(fake_dev)
    heldout_rec = _load(fake_heldout)

    dev_hashes = {_content_hash(r): r["case_id"] for r in dev_records}
    heldout_hashes = {_content_hash(r): r["case_id"] for r in heldout_rec}

    overlap = set(dev_hashes) & set(heldout_hashes)
    assert len(overlap) == 1, (
        f"Expected to detect 1 planted case but found {len(overlap)}. "
        f"The leakage check is not working correctly."
    )
