"""FIXR-001 · the env doctor's own tests.

Deterministic and offline: every credential, socket and binary is monkeypatched, so these tests
never depend on a real key or a running daemon and — the point of the ticket — never skip.
"""
import subprocess

import pytest

from src import doctor


def _ollama_reachable(monkeypatch):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(doctor.socket, "create_connection", lambda *a, **k: _Conn())


def _ffmpeg_present(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "ffmpeg version 6.0\n", ""))


@pytest.fixture
def all_present(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi_test")
    _ollama_reachable(monkeypatch)
    _ffmpeg_present(monkeypatch)


def test_run_checks_covers_the_five_named_dependencies(monkeypatch, all_present):
    labels = [label for label, _, _ in doctor.run_checks()]
    assert labels == ["HF_TOKEN", "GROQ", "NIM", "ollama", "ffmpeg"]


def test_every_check_returns_bool_and_detail(monkeypatch, all_present):
    for label, ok, detail in doctor.run_checks():
        assert isinstance(ok, bool), label
        assert isinstance(detail, str) and detail, label


def test_all_present_exits_zero(monkeypatch, all_present, capsys):
    assert doctor.main() == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    pass_lines = [ln for ln in out.splitlines() if ln.startswith("PASS")]
    assert len(pass_lines) == 5


@pytest.mark.parametrize("var", ["HF_TOKEN", "GROQ_API_KEY", "NVIDIA_API_KEY"])
def test_a_missing_credential_is_fail(monkeypatch, all_present, capsys, var):
    monkeypatch.delenv(var, raising=False)
    if var == "HF_TOKEN":
        monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    assert doctor.main() == 1
    assert "FAIL" in capsys.readouterr().out


def test_hf_token_accepts_the_hub_alias(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_test")
    ok, detail = doctor.check_hf_token()
    assert ok is True
    assert "HUGGING_FACE_HUB_TOKEN" in detail


def test_ollama_unreachable_is_fail(monkeypatch):
    def refused(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(doctor.socket, "create_connection", refused)
    ok, detail = doctor.check_ollama()
    assert ok is False
    assert "ollama" in detail.lower()


def test_ffmpeg_absent_is_fail(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    ok, detail = doctor.check_ffmpeg()
    assert ok is False
    assert "ffmpeg" in detail.lower()


def test_ffmpeg_nonzero_exit_is_fail(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 127, "", "not found"))
    ok, _ = doctor.check_ffmpeg()
    assert ok is False
