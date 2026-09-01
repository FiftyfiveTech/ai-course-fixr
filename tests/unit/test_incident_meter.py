"""FIXR-006: incident_meter prints one summary line and writes incidents.jsonl."""
import json
import re
import time
import pytest
import src.telemetry as tel


@pytest.fixture(autouse=True)
def redirect_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(tel, "INCIDENTS_LOG", tmp_path / "incidents.jsonl")
    monkeypatch.setattr(tel, "RUNS_DIR", tmp_path)


def test_prints_meter_line(capsys):
    with tel.incident_meter("abc123", "text"):
        pass
    out = capsys.readouterr().out
    assert "[METER]" in out
    assert "incident=abc123" in out
    assert "wall_ms=" in out
    assert "cost_usd=0.00" in out
    assert "×realtime=null" in out          # text → no audio_s → null


def test_audio_xrealtime(capsys):
    with tel.incident_meter("audio1", "audio", audio_s=10.0):
        time.sleep(0.01)                    # ~10 ms wall clock
    out = capsys.readouterr().out
    # ×realtime should be a small positive number (wall_ms / 10_000)
    match = re.search(r"×realtime=([\d.]+)x", out)
    assert match, f"×realtime not found in: {out}"
    assert float(match.group(1)) > 0


def test_writes_incidents_jsonl(tmp_path):
    log = tmp_path / "incidents.jsonl"
    import src.telemetry as tel2
    tel2.INCIDENTS_LOG = log
    tel2.RUNS_DIR = tmp_path
    with tel2.incident_meter("ev001", "image"):
        pass
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["incident_id"] == "ev001"
    assert rec["modality"] == "image"
    assert rec["cost_usd"] == 0.0
    assert rec["ok"] is True
    assert "wall_ms" in rec


def test_error_still_logs(tmp_path, capsys):
    log = tmp_path / "incidents.jsonl"
    import src.telemetry as tel3
    tel3.INCIDENTS_LOG = log
    tel3.RUNS_DIR = tmp_path
    with pytest.raises(ValueError):
        with tel3.incident_meter("err1", "text"):
            raise ValueError("boom")
    rec = json.loads(log.read_text().strip())
    assert rec["ok"] is False
    assert "ValueError" in rec["error"]
    out = capsys.readouterr().out
    assert "ERROR=" in out


def test_caller_can_add_fields(tmp_path):
    log = tmp_path / "incidents.jsonl"
    import src.telemetry as tel4
    tel4.INCIDENTS_LOG = log
    tel4.RUNS_DIR = tmp_path
    with tel4.incident_meter("ev002", "text") as inc:
        inc["disposition"] = "RESOLVE"
        inc["evidence_ids"] = ["eid-001", "eid-002"]
    rec = json.loads(log.read_text().strip())
    assert rec["disposition"] == "RESOLVE"
    assert rec["evidence_ids"] == ["eid-001", "eid-002"]
