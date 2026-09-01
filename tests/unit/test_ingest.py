"""FIXR-005: three input kinds, one evidence record shape, a stable content-addressed id.

No network, no weights, no ffmpeg: the two model paths are stubbed at the `arms.stt` / `arms.vision`
seam (the offline-stub path needs no stub at all — deleting the key is the whole test), and the
vision *backend* is exercised once directly with a faked httpx to prove the image really is sent
inline as a data: URI.

The id assertions are the ticket's core: the id is a hash of the RAW input bytes, so the live path
and the offline stub over the same bytes produce the *same* id, and re-submitting an input dedupes.
"""
import hashlib
import json
import types

import httpx
import pytest
from pydantic import ValidationError

from schemas.evidence import EvidenceRecord, mint_id
from src import arms, config, ingest, triage, vision

PNG = b"\x89PNG\r\n\x1a\nfake-png-body"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-body"


# --- text: no model call, the text is the evidence ------------------------------------------

def test_text_becomes_a_verbatim_evidence_record():
    rec = ingest.ingest_text("disk full on prod-3")
    assert rec.kind == "text"
    assert rec.content == "disk full on prod-3"
    assert rec.live is True and rec.origin == "text:verbatim"
    assert rec.id == "ev-txt-" + hashlib.sha256(b"disk full on prod-3").hexdigest()[:12]


def test_identical_text_dedupes_and_different_text_does_not():
    assert ingest.ingest_text("same").id == ingest.ingest_text("same").id
    assert ingest.ingest_text("a").id != ingest.ingest_text("b").id


# --- audio: transcribed by the STT arm (whisper-large-v3-turbo) ------------------------------

def _fake_stt(monkeypatch, transcript):
    monkeypatch.setattr(ingest, "_decode_audio", lambda p: [0.0] * 16)   # no ffmpeg, no decode
    monkeypatch.setattr(arms, "stt", lambda audio, model_id, *, turn_id: transcript)


def test_audio_becomes_evidence_transcribed_live_when_the_key_is_present(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    _fake_stt(monkeypatch, "the disk is full")
    f = tmp_path / "report.mp3"
    f.write_bytes(b"RAWAUDIOBYTES")

    rec = ingest.ingest_audio(f, turn_id="t")

    assert rec.kind == "audio" and rec.live is True
    assert rec.content == "the disk is full"
    assert rec.origin == config.resolve("stt").id           # openai/whisper-large-v3-turbo@groq
    assert rec.id == "ev-aud-" + hashlib.sha256(b"RAWAUDIOBYTES").hexdigest()[:12]


def test_audio_falls_to_the_offline_stub_without_a_key_but_keeps_the_same_id(monkeypatch, tmp_path):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # No stt/decode stub: the stub path must not call either. If it does, the test errors loudly.
    monkeypatch.setattr(arms, "stt", lambda *a, **k: pytest.fail("stub path called arms.stt"))
    f = tmp_path / "report.mp3"
    f.write_bytes(b"RAWAUDIOBYTES")

    rec = ingest.ingest_audio(f, turn_id="t")

    assert rec.live is False and rec.origin == "offline-stub"
    assert "offline stub" in rec.content and "GROQ_API_KEY" in rec.content
    # The whole point of hashing the raw input: the id does not depend on whether a model ran.
    assert rec.id == "ev-aud-" + hashlib.sha256(b"RAWAUDIOBYTES").hexdigest()[:12]


# --- screenshot: read by the vision arm (Nemotron-Nano-VL) -----------------------------------

def test_screenshot_becomes_evidence_read_live_when_the_vlm_is_available(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "_live", lambda arm: True)      # ollama daemon up, model pulled
    monkeypatch.setattr(arms, "vision",
                        lambda image, media_type, prompt, model_id, *, turn_id: "ERROR 0x28")
    f = tmp_path / "error.png"
    f.write_bytes(PNG)

    rec = ingest.ingest_screenshot(f, turn_id="t")

    assert rec.kind == "screenshot" and rec.live is True
    assert rec.content == "ERROR 0x28"
    assert rec.origin == config.resolve("vision").id           # hf.co/...Qwen2.5-VL...@ollama
    assert rec.id == "ev-img-" + hashlib.sha256(PNG).hexdigest()[:12]


def test_screenshot_falls_to_the_offline_stub_when_the_vlm_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "_live", lambda arm: False)     # daemon down or model not pulled
    monkeypatch.setattr(arms, "vision", lambda *a, **k: pytest.fail("stub path called arms.vision"))
    f = tmp_path / "error.png"
    f.write_bytes(PNG)

    rec = ingest.ingest_screenshot(f, turn_id="t")

    assert rec.live is False and rec.origin == "offline-stub"
    assert "ollama unavailable" in rec.content
    assert rec.id == "ev-img-" + hashlib.sha256(PNG).hexdigest()[:12]


@pytest.mark.parametrize("raw,expected", [
    (PNG, "image/png"),
    (JPEG, "image/jpeg"),
])
def test_media_type_is_read_from_the_bytes_not_the_extension(raw, expected, tmp_path):
    f = tmp_path / "screenshot.bin"        # deliberately not a .png/.jpg extension
    f.write_bytes(raw)
    assert ingest._media_type(f, raw) == expected


@pytest.mark.parametrize("pulled", [True, False])
def test_live_defers_to_the_ollama_check_for_the_local_vision_arm(monkeypatch, pulled):
    """The local arm needs no key, so liveness is 'is the daemon up and the model pulled', which is
    the one question `_ollama_pulled` answers — asserted without touching a real daemon."""
    monkeypatch.setattr(ingest, "_ollama_pulled", lambda arm: pulled)
    assert ingest._live(config.resolve("vision")) is pulled


# --- the vision backend actually sends the image inline --------------------------------------

def test_vision_backend_sends_the_image_as_a_data_uri_and_returns_the_read(monkeypatch):
    """The fake-backend tests in test_arms.py cannot see this — they replace the adapter. This one
    keeps the real adapter and fakes httpx, so the request shape NIM's VLM needs is asserted."""
    captured = {}

    class Resp:
        status_code = 200
        headers = {}

        def json(self):
            return {"choices": [{"message": {"content": "DISK FULL: C:"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4}}

        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"], captured["body"] = url, json
        return Resp()

    monkeypatch.setattr(vision, "httpx", types.SimpleNamespace(
        post=fake_post, Headers=httpx.Headers, HTTPStatusError=httpx.HTTPStatusError))

    arm = config.resolve("vision")
    rec = {}
    out = vision.openai_vision(arm, {"image": PNG, "media_type": "image/png",
                                     "prompt": "read the screen"}, rec)

    assert out == "DISK FULL: C:"
    assert captured["body"]["model"] == "hf.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF:Q4_K_M"
    content = captured["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "read the screen"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert rec["read_chars"] == len("DISK FULL: C:")


# --- the record type carries no confidence (FIXR-021's boundary, asserted early) --------------

def test_evidence_cannot_carry_confidence():
    assert "confidence" not in EvidenceRecord.model_fields
    with pytest.raises(ValidationError):
        EvidenceRecord(id="ev-txt-x", kind="text", source="s", content="c",
                       origin="o", live=True, confidence=0.9)


def test_mint_id_refuses_an_unknown_kind():
    with pytest.raises(ValueError):
        mint_id("video", b"x")


# --- the CLI collects all three and lists the ids it used ------------------------------------

def test_run_over_all_three_lists_every_evidence_id_in_order(monkeypatch, tmp_path):
    # Force every model-backed path to its offline stub, so this is hermetic regardless of whether a
    # Groq key or a local ollama daemon happen to be present on the machine running the suite.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(ingest, "_ollama_pulled", lambda arm: False)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"AUDIO")
    shot = tmp_path / "s.png"
    shot.write_bytes(PNG)

    resp = triage.run(text="prod-3 is down", audio=audio, screenshot=shot, turn_id="t")

    ids = resp["evidence_ids"]
    assert [e["id"] for e in resp["evidence"]] == ids, "evidence_ids must match the records used"
    assert len(ids) == 3
    assert ids[0].startswith("ev-txt-")
    assert ids[1].startswith("ev-aud-")
    assert ids[2].startswith("ev-img-")


def test_main_prints_json_whose_evidence_ids_are_the_gate(monkeypatch, capsys):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    rc = triage.main(["--text", "boom"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["evidence_ids"] == [out["evidence"][0]["id"]]


def test_main_refuses_a_run_with_no_input(capsys):
    with pytest.raises(SystemExit):
        triage.main([])
