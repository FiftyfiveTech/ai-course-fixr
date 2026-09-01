"""FIXR-001 · env doctor.

`make doctor` runs this. It checks every external dependency the pipeline needs, prints PASS or
FAIL for each, and exits non-zero if any one is missing.

Why it has to be strict. The unit suite runs fully offline — every provider call is mocked — so a
missing credential or a stopped daemon does not *fail* a test, it makes the test that would have
exercised that dependency **skip**, and a skip is coloured like a pass. "A silent test skip is a
FAIL" is the whole ticket: this doctor is the one place where "the environment cannot actually run
the system" is made loud. It checks the things a mocked test cannot see — a credential is present,
a daemon answers, a binary is on PATH — and refuses to exit 0 while any of them is absent.

It does presence/reachability, not a paid liveness probe: checking a Groq key works would mean a
call, and zero spend is a hard constraint. Presence of the key is what decides whether a live arm
*could* run, which is exactly what a silent skip hides.
"""
import os
import shutil
import socket
import subprocess
import sys
from urllib.parse import urlparse

from src import config


def _env_present(*names):
    """Return the first of `names` that is set to a non-blank value, else None.

    Several credentials have more than one accepted variable name (huggingface_hub reads either
    HF_TOKEN or HUGGING_FACE_HUB_TOKEN); a check passes if any of its aliases is filled.
    """
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return name
    return None


def check_hf_token():
    """HF_TOKEN — needed to download gated Hugging Face repos."""
    found = _env_present("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
    if found:
        return True, f"{found} set — gated HF repo downloads are authorised"
    return False, ("HF_TOKEN not set — gated repos (e.g. pyannote, some Llama tokenizers) 401 on "
                   "download. Add it to .env; names are listed in .env.example.")


def check_groq():
    """GROQ_API_KEY — the Groq free tier that serves the default STT and LLM arms."""
    if _env_present("GROQ_API_KEY"):
        return True, ("GROQ_API_KEY set — Groq free tier (whisper-large-v3-turbo STT, "
                      "gpt-oss-120b LLM) can be called")
    return False, ("GROQ_API_KEY not set — the default STT and LLM arms cannot run. Add it to "
                   ".env. Do not substitute a paid endpoint; zero spend is a hard constraint.")


def check_nim():
    """NVIDIA_API_KEY — the NVIDIA NIM free tier that serves the hosted LLM fallback arm."""
    if _env_present("NVIDIA_API_KEY"):
        return True, "NVIDIA_API_KEY set — NIM free tier (gpt-oss-20b LLM fallback) can be called"
    return False, ("NVIDIA_API_KEY not set — the hosted LLM fallback arm cannot run. Add it to "
                   ".env.")


def check_ollama():
    """ollama daemon — the local LLM fallback arm speaks HTTP to it on localhost."""
    parsed = urlparse(config.OLLAMA)
    host, port = parsed.hostname or "localhost", parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True, f"ollama daemon reachable at {host}:{port} — local LLM fallback available"
    except OSError as exc:
        return False, (f"ollama daemon not reachable at {host}:{port} ({type(exc).__name__}) — "
                       f"start it with `ollama serve`. The local fallback arm cannot run without "
                       f"it.")


def check_ffmpeg():
    """ffmpeg on PATH — audio decoding for the whisper STT path and the mp3 fixtures."""
    path = shutil.which("ffmpeg")
    if not path:
        return False, ("ffmpeg not on PATH — audio decode (whisper input, tests/fixtures/*.mp3) "
                       "cannot run. Install it (winget/brew/apt install ffmpeg).")
    try:
        proc = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"ffmpeg at {path} but `ffmpeg -version` failed ({type(exc).__name__})."
    if proc.returncode != 0:
        return False, f"ffmpeg at {path} exited {proc.returncode} on -version."
    banner = (proc.stdout or proc.stderr).splitlines()
    return True, f"{banner[0].strip() if banner else 'ffmpeg present'}  ({path})"


# Ordered exactly as the ticket names them: HF_TOKEN, Groq, NIM, ollama, ffmpeg.
CHECKS = (
    ("HF_TOKEN", check_hf_token),
    ("GROQ", check_groq),
    ("NIM", check_nim),
    ("ollama", check_ollama),
    ("ffmpeg", check_ffmpeg),
)


def run_checks():
    """-> list of (label, ok, detail), one per dependency, in ticket order."""
    return [(label, *fn()) for label, fn in CHECKS]


def main():
    """Print the table, return the process exit code (0 all pass, 1 any fail)."""
    config.utf8_console()
    results = run_checks()
    width = max(len(label) for label, _, _ in results)
    print("FIXR env doctor — every dependency must PASS, or a live test skips instead of failing.")
    print()
    failed = 0
    for label, ok, detail in results:
        if not ok:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'}  {label.ljust(width)}  {detail}")
    print()
    if failed:
        print(f"{failed} of {len(results)} FAILED. Fix the above before the gates — zero spend "
              f"means a real free-tier key, never a paid endpoint.")
        return 1
    print(f"all {len(results)} dependencies present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
