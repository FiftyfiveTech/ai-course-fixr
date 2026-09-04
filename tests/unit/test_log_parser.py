"""FIXR-017: log parser — structured fields, one evidence record per non-blank line.

Two acceptance criteria:

  log lines get evidence ids       each non-blank line produces an EvidenceRecord with a stable
                                   ev-txt-* id derived from the raw bytes of that line.

  a claim citing a missing id fails  validate_provenance raises DanglingEidError when a cited id
                                     was not produced by ingest_log for this log.

No network, no models, no filesystem writes needed. The parser is pure-Python.
"""
import hashlib

import pytest

from schemas.evidence import EvidenceRecord
from src.log_parser import LogLine, ingest_log, parse_line
from src.validators.provenance import DanglingEidError, validate_provenance

# ---------------------------------------------------------------------------
# Sample log text used across several tests
# ---------------------------------------------------------------------------

SAMPLE_LOG = """\
2024-01-15T09:12:34.567 ERROR [prod-3] OSError: No space left on device
2024-01-15T09:12:35.100 WARN  [prod-3] Retry 1/3 failed
2024-01-15T09:12:36.002 INFO  [prod-3] Disk usage: /dev/sda1 100%
"""

# ---------------------------------------------------------------------------
# ingest_log: evidence ids
# ---------------------------------------------------------------------------


def test_each_non_blank_line_becomes_one_evidence_record():
    records = ingest_log(SAMPLE_LOG)
    assert len(records) == 3


def test_blank_lines_are_skipped():
    text = "\nline one\n\nline two\n\n"
    records = ingest_log(text)
    assert len(records) == 2


def test_all_records_are_evidence_record_instances():
    for rec in ingest_log(SAMPLE_LOG):
        assert isinstance(rec, EvidenceRecord)


def test_each_record_kind_is_text():
    for rec in ingest_log(SAMPLE_LOG):
        assert rec.kind == "text"


def test_each_record_origin_is_log_parsed():
    for rec in ingest_log(SAMPLE_LOG):
        assert rec.origin == "log:parsed"


def test_each_record_is_live():
    for rec in ingest_log(SAMPLE_LOG):
        assert rec.live is True


def test_each_record_id_starts_with_ev_txt():
    for rec in ingest_log(SAMPLE_LOG):
        assert rec.id.startswith("ev-txt-")


def test_record_id_is_stable_hash_of_raw_line_bytes():
    """The id must be exactly mint_id("text", line.encode("utf-8"))."""
    lines = [l for l in SAMPLE_LOG.splitlines() if l.strip()]
    records = ingest_log(SAMPLE_LOG)
    for line, rec in zip(lines, records):
        expected = "ev-txt-" + hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]
        assert rec.id == expected


def test_identical_lines_produce_the_same_id():
    text = "same log line\nsame log line\n"
    records = ingest_log(text)
    assert records[0].id == records[1].id


def test_different_lines_produce_different_ids():
    records = ingest_log(SAMPLE_LOG)
    ids = [r.id for r in records]
    assert len(set(ids)) == len(ids), "all three lines are distinct — ids must differ"


def test_record_content_is_the_verbatim_line():
    lines = [l for l in SAMPLE_LOG.splitlines() if l.strip()]
    records = ingest_log(SAMPLE_LOG)
    for line, rec in zip(lines, records):
        assert rec.content == line


def test_source_label_is_passed_through():
    records = ingest_log("a line\n", source="incident.log")
    assert records[0].source == "incident.log"


def test_default_source_is_log_inline():
    records = ingest_log("a line\n")
    assert records[0].source == "log:inline"


# ---------------------------------------------------------------------------
# THE ACCEPTANCE CRITERION: a claim citing a missing line id fails
# ---------------------------------------------------------------------------


def test_citing_a_real_line_id_passes_provenance():
    records = ingest_log(SAMPLE_LOG)
    minted = {r.id for r in records}
    cited = [records[0].id]          # first line — definitely minted
    validate_provenance(cited, minted)   # must not raise


def test_citing_all_line_ids_passes_provenance():
    records = ingest_log(SAMPLE_LOG)
    minted = {r.id for r in records}
    cited = [r.id for r in records]
    validate_provenance(cited, minted)   # must not raise


def test_citing_a_line_not_in_the_log_raises_dangling_eid_error():
    records = ingest_log(SAMPLE_LOG)
    minted = {r.id for r in records}
    fabricated_id = "ev-txt-000000000000"   # never produced by this log
    with pytest.raises(DanglingEidError) as exc_info:
        validate_provenance([fabricated_id], minted)
    assert fabricated_id in str(exc_info.value)


def test_citing_a_real_and_a_missing_id_together_raises():
    records = ingest_log(SAMPLE_LOG)
    minted = {r.id for r in records}
    fabricated_id = "ev-txt-deadbeef0000"
    with pytest.raises(DanglingEidError):
        validate_provenance([records[0].id, fabricated_id], minted)


def test_citing_an_id_from_a_different_log_raises():
    """An id produced by parsing log A is not valid provenance for log B."""
    records_a = ingest_log("alpha error\n")
    records_b = ingest_log("beta error\n")
    minted_b = {r.id for r in records_b}
    with pytest.raises(DanglingEidError):
        validate_provenance([records_a[0].id], minted_b)


# ---------------------------------------------------------------------------
# parse_line: structured field extraction
# ---------------------------------------------------------------------------


def test_iso_timestamp_and_level_and_component_are_extracted():
    line = "2024-01-15T09:12:34.567 ERROR [prod-3] OSError: No space left on device"
    pl = parse_line(line)
    assert pl.timestamp == "2024-01-15T09:12:34.567"
    assert pl.level == "ERROR"
    assert pl.component == "prod-3"
    assert "OSError" in pl.message


def test_warn_alias_is_normalised():
    pl = parse_line("2024-01-15 09:12:35 WARNING [app] slow query")
    assert pl.level == "WARN"


def test_bracket_prefix_format():
    pl = parse_line("[ERROR] disk full on prod-3")
    assert pl.level == "ERROR"
    assert "disk full" in pl.message


def test_level_colon_format():
    pl = parse_line("ERROR: connection refused")
    assert pl.level == "ERROR"
    assert "connection refused" in pl.message


def test_raw_line_is_always_preserved():
    line = "2024-01-15T09:12:34 INFO [svc] started"
    pl = parse_line(line)
    assert pl.raw == line


def test_unrecognised_line_uses_full_text_as_message():
    line = "this is not a structured log line at all"
    pl = parse_line(line)
    assert isinstance(pl, LogLine)
    assert pl.message == line
    assert pl.timestamp is None
    assert pl.level is None


def test_bare_level_keyword_is_found_in_unstructured_line():
    pl = parse_line("something went wrong: FATAL crash detected")
    assert pl.level == "FATAL"
