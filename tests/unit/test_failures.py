"""FIXR-009: failure tests — corrupt image, empty log, audio with no speech, missing eid.

Done when: pytest tests/unit -rs green with no silent skips; each case fails loudly with a useful
message.

Four failure modes, four planted violations:
  1. Corrupt image       — bytes that are not a recognisable image format raise ValueError.
  2. Empty log           — empty or whitespace-only text raises ValueError.
  3. Audio with no speech — STT returns an empty transcript; ingest_audio raises ValueError.
  4. Missing evidence id — a cited eid that was never minted raises DanglingEidError.

Each section has one happy-path assertion (proves the failure check is narrowly targeted) and one
planted-violation assertion (proves the check fires and the message is informative).
"""
import pytest

from src import arms, ingest
from src.validators.provenance import DanglingEidError, validate_provenance

PNG = b"\x89PNG\r\n\x1a\nfake-png-body"
CORRUPT = b"NOTANIMAGE\x00\x01\x02\x03"   # no magic bytes, no known extension when unnamed


# ===========================================================================
# 1. Corrupt image
# ===========================================================================

class TestCorruptImage:

    def test_valid_png_bytes_pass(self, monkeypatch, tmp_path):
        """Happy path: a real PNG magic header does not raise."""
        monkeypatch.setattr(ingest, "_live", lambda arm: False)
        f = tmp_path / "ok.png"
        f.write_bytes(PNG)
        rec = ingest.ingest_screenshot(f, turn_id="t")
        assert rec.kind == "screenshot"

    def test_corrupt_bytes_raise_value_error(self, tmp_path):
        """Planted: bytes with no recognised magic and no known extension raise."""
        f = tmp_path / "corrupt.bin"
        f.write_bytes(CORRUPT)
        with pytest.raises(ValueError) as exc_info:
            ingest.ingest_screenshot(f, turn_id="t")
        msg = str(exc_info.value)
        assert "not a recognisable image format" in msg
        assert "corrupt.bin" in msg

    def test_corrupt_message_includes_first_bytes(self, tmp_path):
        """The error message shows the first bytes so the caller can diagnose the file."""
        f = tmp_path / "bad.bin"
        f.write_bytes(CORRUPT)
        with pytest.raises(ValueError) as exc_info:
            ingest.ingest_screenshot(f, turn_id="t")
        assert "first 8 bytes" in str(exc_info.value)


# ===========================================================================
# 2. Empty log
# ===========================================================================

class TestEmptyLog:

    def test_non_empty_text_passes(self):
        """Happy path: a real log line does not raise."""
        rec = ingest.ingest_text("disk full on prod-3")
        assert rec.kind == "text"

    def test_empty_string_raises(self):
        """Planted: an empty string is not observable evidence."""
        with pytest.raises(ValueError) as exc_info:
            ingest.ingest_text("")
        assert "empty" in str(exc_info.value).lower()

    def test_whitespace_only_raises(self):
        """Planted: whitespace-only text (e.g. a blank log file) also raises."""
        with pytest.raises(ValueError):
            ingest.ingest_text("   \n\t  ")

    def test_error_names_the_source(self):
        """The error message includes the source label so the caller knows which file was blank."""
        with pytest.raises(ValueError) as exc_info:
            ingest.ingest_text("", source="logs/app.log")
        assert "logs/app.log" in str(exc_info.value)


# ===========================================================================
# 3. Audio with no speech
# ===========================================================================

class TestAudioNoSpeech:

    def _fake_stt(self, monkeypatch, transcript):
        monkeypatch.setattr(ingest, "_decode_audio", lambda p: [0.0] * 16)
        monkeypatch.setattr(arms, "stt", lambda audio, model_id, *, turn_id: transcript)

    def test_normal_transcript_passes(self, monkeypatch, tmp_path):
        """Happy path: a real transcript does not raise."""
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        self._fake_stt(monkeypatch, "the disk is full")
        f = tmp_path / "report.mp3"
        f.write_bytes(b"AUDIODATA")
        rec = ingest.ingest_audio(f, turn_id="t")
        assert rec.content == "the disk is full"

    def test_empty_transcript_raises(self, monkeypatch, tmp_path):
        """Planted: STT returns empty string — silent audio is not evidence."""
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        self._fake_stt(monkeypatch, "")
        f = tmp_path / "silent.mp3"
        f.write_bytes(b"AUDIODATA")
        with pytest.raises(ValueError) as exc_info:
            ingest.ingest_audio(f, turn_id="t")
        msg = str(exc_info.value)
        assert "empty transcript" in msg
        assert "silent.mp3" in msg

    def test_whitespace_only_transcript_raises(self, monkeypatch, tmp_path):
        """Planted: a transcript of only whitespace (whisper on silence) also raises."""
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        self._fake_stt(monkeypatch, "   \n  ")
        f = tmp_path / "noise.mp3"
        f.write_bytes(b"AUDIODATA")
        with pytest.raises(ValueError):
            ingest.ingest_audio(f, turn_id="t")

    def test_error_names_the_arm(self, monkeypatch, tmp_path):
        """The error message names the arm so the caller can trace which model was responsible."""
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        self._fake_stt(monkeypatch, "")
        f = tmp_path / "report.mp3"
        f.write_bytes(b"AUDIODATA")
        with pytest.raises(ValueError) as exc_info:
            ingest.ingest_audio(f, turn_id="t")
        assert "whisper" in str(exc_info.value).lower()


# ===========================================================================
# 4. Missing evidence id raises
# ===========================================================================

class TestMissingEvidenceIdRaises:

    def test_all_cited_eids_present_passes(self):
        """Happy path: every cited eid was minted."""
        minted = {"ev-txt-aabbcc112233", "ev-img-001122334455"}
        validate_provenance(["ev-txt-aabbcc112233"], minted)  # must not raise

    def test_dangling_eid_raises_dangling_eid_error(self):
        """Planted: a cited eid that was never minted raises DanglingEidError."""
        minted = {"ev-txt-aabbcc112233"}
        with pytest.raises(DanglingEidError) as exc_info:
            validate_provenance(["ev-txt-aabbcc112233", "ev-img-DANGLING0000"], minted)
        assert "ev-img-DANGLING0000" in str(exc_info.value)

    def test_error_is_a_value_error(self):
        """DanglingEidError is a ValueError so callers using except ValueError: catch it."""
        with pytest.raises(ValueError):
            validate_provenance(["ev-img-GHOST000001"], set())

    def test_error_carries_the_dangling_set(self):
        """The structured .dangling field lets callers log which eids were fabricated."""
        minted = {"ev-txt-aabbcc112233"}
        with pytest.raises(DanglingEidError) as exc_info:
            validate_provenance(["ev-img-GHOST000001", "ev-img-GHOST000002"], minted)
        assert exc_info.value.dangling == {"ev-img-GHOST000001", "ev-img-GHOST000002"}
