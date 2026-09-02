"""FIXR-008: two diagnostic arms behind one interface, to isolate the vision contribution.

The `vision` arm reads a screenshot with the VLM; the `text-only` arm suppresses that read. Both
answer the *same* case through the same `triage.run()`, and — because the evidence id is a hash of
the raw input bytes, not of the model's reading — both mint the *same* ids. So the two arms line up
record-for-record and the only field that moves is the screenshot's content. That difference, and
nothing else, is what the vision leg contributed.

No network, no weights: the vision path is stubbed at the `arms.vision` seam, and the text-only
path is asserted to never reach it.
"""
import hashlib
import json

import pytest

from src import arms, ingest, triage

PNG = b"\x89PNG\r\n\x1a\nfake-png-body"


# --- ingest: text-only suppresses the read but keeps the id ----------------------------------

def test_text_only_screenshot_is_not_read_but_keeps_the_same_id(monkeypatch, tmp_path):
    # If the arm looks at the pixels at all, this fails loudly — the whole point is that it does not.
    monkeypatch.setattr(arms, "vision", lambda *a, **k: pytest.fail("text-only arm read the image"))
    # Even a live, available VLM must be left untouched: this is a choice, not a fallback.
    monkeypatch.setattr(ingest, "_live", lambda arm: True)
    f = tmp_path / "error.png"
    f.write_bytes(PNG)

    rec = ingest.ingest_screenshot(f, turn_id="t", read=False)

    assert rec.kind == "screenshot" and rec.live is False
    assert rec.origin == "text-only-arm"
    assert "vision suppressed" in rec.content
    # The id is over the raw bytes, so it is identical to what the vision arm would mint.
    assert rec.id == "ev-img-" + hashlib.sha256(PNG).hexdigest()[:12]


def test_both_arms_mint_the_same_screenshot_id(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "_live", lambda arm: True)
    monkeypatch.setattr(arms, "vision",
                        lambda image, media_type, prompt, model_id, *, turn_id: "ERROR 0x28")
    f = tmp_path / "error.png"
    f.write_bytes(PNG)

    read = ingest.ingest_screenshot(f, turn_id="t", read=True)
    suppressed = ingest.ingest_screenshot(f, turn_id="t", read=False)

    assert read.id == suppressed.id                 # same case, same evidence id
    assert read.content != suppressed.content        # the vision contribution is the difference
    assert read.content == "ERROR 0x28"
    assert read.live is True and suppressed.live is False


# --- run(): one flag switches arms; both answer the same case --------------------------------

def _hermetic(monkeypatch):
    """Force every model-backed path offline so the test is the same on any machine."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(ingest, "_ollama_pulled", lambda arm: False)


def test_both_arms_answer_the_same_case_with_identical_ids(monkeypatch, tmp_path):
    _hermetic(monkeypatch)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"AUDIO")
    shot = tmp_path / "s.png"
    shot.write_bytes(PNG)
    case = dict(text="prod-3 is down", audio=audio, screenshot=shot, turn_id="t")

    vision = triage.run(arm="vision", **case)
    text_only = triage.run(arm="text-only", **case)

    # Same case -> same evidence ids, in the same order, under either arm.
    assert vision["evidence_ids"] == text_only["evidence_ids"]
    assert len(vision["evidence_ids"]) == 3
    assert vision["arm"] == "vision" and text_only["arm"] == "text-only"

    # Text and audio records are byte-identical between the arms — only vision was ablated.
    assert vision["evidence"][0] == text_only["evidence"][0]        # text
    assert vision["evidence"][1] == text_only["evidence"][1]        # audio

    # The screenshot record is where — and the only place where — the two arms part.
    v_shot, t_shot = vision["evidence"][2], text_only["evidence"][2]
    assert v_shot["id"] == t_shot["id"]
    assert v_shot["content"] != t_shot["content"]
    assert t_shot["origin"] == "text-only-arm" and t_shot["live"] is False


def test_text_only_arm_needs_no_vision_at_all(monkeypatch, tmp_path):
    _hermetic(monkeypatch)
    # A daemon check would be a call the text-only arm has no business making; fail if it is made.
    monkeypatch.setattr(ingest, "_ollama_pulled", lambda arm: pytest.fail("text-only touched ollama"))
    monkeypatch.setattr(arms, "vision", lambda *a, **k: pytest.fail("text-only called the VLM"))
    shot = tmp_path / "s.png"
    shot.write_bytes(PNG)

    resp = triage.run(arm="text-only", screenshot=shot, turn_id="t")

    assert resp["evidence"][0]["origin"] == "text-only-arm"


def test_a_case_with_no_screenshot_is_identical_under_both_arms(monkeypatch):
    _hermetic(monkeypatch)
    v = triage.run(arm="vision", text="boom", turn_id="t")
    t = triage.run(arm="text-only", text="boom", turn_id="t")
    # With nothing to see, the vision leg contributes nothing — the arms agree everywhere but `arm`.
    assert v["evidence"] == t["evidence"]
    assert v["evidence_ids"] == t["evidence_ids"]


def test_run_refuses_an_unknown_arm():
    with pytest.raises(ValueError):
        triage.run(text="x", arm="telepathy", turn_id="t")


# --- the CLI flag ---------------------------------------------------------------------------

def test_cli_arm_flag_switches_the_screenshot_read(monkeypatch, capsys, tmp_path):
    _hermetic(monkeypatch)
    shot = tmp_path / "s.png"
    shot.write_bytes(PNG)

    triage.main(["--arm", "text-only", "--screenshot", str(shot)])
    out = json.loads(capsys.readouterr().out)

    assert out["arm"] == "text-only"
    assert out["evidence"][0]["origin"] == "text-only-arm"
    assert out["evidence"][0]["id"] == "ev-img-" + hashlib.sha256(PNG).hexdigest()[:12]


def test_cli_defaults_to_the_vision_arm(monkeypatch, capsys):
    _hermetic(monkeypatch)
    triage.main(["--text", "boom"])
    out = json.loads(capsys.readouterr().out)
    assert out["arm"] == "vision"


def test_cli_rejects_an_unknown_arm(capsys):
    with pytest.raises(SystemExit):
        triage.main(["--text", "boom", "--arm", "telepathy"])
