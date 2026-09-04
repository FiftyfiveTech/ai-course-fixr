"""FIXR-016: OCR arm comparison — stepfun-ai/GOT-OCR2_0 vs Qwen2.5-VL-3B (ollama).

Two metrics per test case:
  fact_recall     fraction of ground-truth tokens that appear in the model output (0–1).
                  A recall of 1.0 means every token the screenshot actually contains was read.
  invented_ids    count of technical identifiers in the output that do NOT appear in the ground
                  truth. Technical identifiers are hex codes, version strings, Unix paths, numeric
                  measurements and similar specific tokens. A count > 0 means the model is adding
                  identifiers from nowhere — hallucination at the level the triage pipeline cares
                  about most.

Fixture images are rendered PNGs with known text, stored in tests/fixtures/ocr_cases/.
Both arms fall back to a labelled offline stub when unavailable, so the table always prints.

Usage (from repo root):
    python scripts/ocr_compare.py
"""
from __future__ import annotations

import base64
import io
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.config import OLLAMA, resolve

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ocr_cases"

# ---------------------------------------------------------------------------
# Test cases: filename -> ground-truth text written on the PNG
# ---------------------------------------------------------------------------

CASES = [
    {
        "name": "error_dialog",
        "file": FIXTURES / "error_dialog.png",
        "truth": (
            "OSError: [Errno 28] No space left on device\n"
            "Path: /var/log/syslog\n"
            "Process: rsyslogd PID 1234"
        ),
    },
    {
        "name": "http_error",
        "file": FIXTURES / "http_error.png",
        "truth": (
            "HTTP 503 Service Unavailable\n"
            "Server: nginx/1.24.0\n"
            "Retry-After: 60"
        ),
    },
    {
        "name": "disk_usage",
        "file": FIXTURES / "disk_usage.png",
        "truth": (
            "Filesystem: /dev/sda1\n"
            "Size: 500G  Used: 499G  Avail: 0  Use%: 100%\n"
            "Mounted on: /var/log"
        ),
    },
    {
        "name": "crash_report",
        "file": FIXTURES / "crash_report.png",
        "truth": (
            "Application: webapp v2.3.1\n"
            "Exit code: 0xc0000005\n"
            "Host: prod-3.example.com\n"
            "Timestamp: 2024-01-15T09:12:34"
        ),
    },
]

# How long to wait for each Qwen2.5-VL call. The 3B GGUF model runs on CPU (no VRAM),
# so each call takes ~135 s on a modern machine. The arm's default LOCAL_TIMEOUT_S is 120 s,
# which is too short; the comparison script uses its own ceiling.
_QWEN_TIMEOUT_S = 300

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

# Technical identifiers that a model should not invent: hex codes, version strings,
# Unix paths, numeric measurements, hostnames with dots, RFC-style codes, PID-style numbers.
_ID_RE = re.compile(
    r"0x[0-9a-fA-F]+"           # hex literal
    r"|/[a-z][a-z0-9_/.-]{2,}"  # Unix path
    r"|\d+\.\d+[\.\d]*"         # version or decimal number
    r"|[A-Z][a-z]+Error"        # Python exception class
    r"|\b\d{3,}\b"              # three-or-more-digit number
    r"|\b[a-z][a-z0-9-]+\.[a-z]{2,}\b"  # hostname / domain
)


def fact_recall(truth: str, output: str) -> float:
    """Fraction of ground-truth tokens found (case-insensitive substring) in the output."""
    tokens = truth.lower().split()
    if not tokens:
        return 1.0
    out_lower = output.lower()
    found = sum(1 for t in tokens if t in out_lower)
    return found / len(tokens)


def invented_ids(truth: str, output: str) -> int:
    """Count of technical identifiers in `output` that do not appear in `truth`."""
    truth_ids = {m.group().lower() for m in _ID_RE.finditer(truth)}
    out_ids   = {m.group().lower() for m in _ID_RE.finditer(output)}
    return len(out_ids - truth_ids)


# ---------------------------------------------------------------------------
# Arm runners: each returns (text, wall_ms, live)
# ---------------------------------------------------------------------------

def _ollama_has_model(provider_model: str) -> bool:
    """True if the ollama daemon is up and the given model is pulled."""
    import httpx
    root = OLLAMA.rsplit("/v1", 1)[0]
    try:
        r = httpx.get(f"{root}/api/tags", timeout=3)
        r.raise_for_status()
    except Exception:
        return False
    pulled = {m.get("name", "") for m in (r.json().get("models") or [])}
    return provider_model in pulled


def _got_ocr2_cached(repo_id: str) -> bool:
    """True if the GOT-OCR2 model weights are in the HF hub cache (no incomplete blobs)."""
    from huggingface_hub import try_to_load_from_cache
    # config.json being present only means the metadata was fetched; weights may still be
    # downloading. Check for the safetensors weight file and no .incomplete blobs.
    weight_sentinel = try_to_load_from_cache(repo_id, "model.safetensors")
    if weight_sentinel is None:
        return False
    # Also verify there are no .incomplete blobs in the hub cache dir, which would mean a
    # partial download is in flight and loading would fail or produce garbage.
    import glob, os
    cache_dir = os.path.dirname(os.path.dirname(weight_sentinel))
    return len(glob.glob(os.path.join(cache_dir, "blobs", "*.incomplete"))) == 0


def run_qwen_vlm(image_bytes: bytes, media_type: str) -> tuple[str, float, bool]:
    """Run Qwen2.5-VL via the ollama daemon. Returns (text, wall_ms, live)."""
    arm = resolve("vision", "qwen2.5-vl")
    if not _ollama_has_model(arm.provider_model):
        stub = f"[offline stub] {arm.id} not called (daemon down or model not pulled)"
        return stub, 0.0, False

    import httpx

    # Direct read instruction — shorter than the full extract_screenshot_v1 prompt so
    # the model reaches the text faster on CPU.
    prompt = "Read all text visible in this image exactly as written, preserving identifiers, numbers, and paths."
    data_uri = f"data:{media_type};base64,{base64.b64encode(image_bytes).decode()}"
    body = {
        "model": arm.provider_model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
        "temperature": 0.0,
        "max_tokens": 256,
    }
    t0 = time.perf_counter()
    r = httpx.post(f"{arm.api_base}/chat/completions",
                   headers={"Content-Type": "application/json"},
                   json=body, timeout=_QWEN_TIMEOUT_S)
    wall_ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    return text, wall_ms, True


def run_got_ocr2(image_bytes: bytes) -> tuple[str, float, bool]:
    """Run stepfun-ai/GOT-OCR2_0 in-process via transformers. Returns (text, wall_ms, live).

    GOT-OCR2_0 ships custom code (`modeling_GOT.py`) and must be loaded with
    `trust_remote_code=True`. Its `model.chat()` API takes an image file path, so the
    raw bytes are written to a temp file, the call is made, and the file is removed.
    """
    import os
    import tempfile
    import torch
    from transformers import AutoModel, AutoTokenizer

    arm = resolve("vision", "got-ocr2")
    if not _got_ocr2_cached(arm.repo_id):
        stub = (f"[offline stub] {arm.id} not called "
                f"(weights not in HF cache — run: "
                f"huggingface-cli download {arm.repo_id})")
        return stub, 0.0, False

    if not torch.cuda.is_available():
        stub = (f"[offline stub] {arm.id} not called "
                f"(stepfun-ai/GOT-OCR2_0 hardcodes .cuda() — GPU required)")
        return stub, 0.0, False

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(
        arm.repo_id, trust_remote_code=True, use_fast=False
    )
    model = AutoModel.from_pretrained(
        arm.repo_id,
        trust_remote_code=True,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        pad_token_id=tokenizer.eos_token_id,
    )
    if torch.cuda.is_available():
        model = model.cuda()
    model.eval()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        t0 = time.perf_counter()
        with torch.inference_mode():
            text = model.chat(tokenizer, tmp_path, ocr_type="ocr")
        wall_ms = (time.perf_counter() - t0) * 1000
    finally:
        os.unlink(tmp_path)

    return (text or "").strip(), wall_ms, True


# ---------------------------------------------------------------------------
# Table printer
# ---------------------------------------------------------------------------

def run_comparison() -> bool:
    """Run both arms on all cases; print results table. -> True when both arms are live."""
    arm_qwen = resolve("vision", "qwen2.5-vl")
    arm_got  = resolve("vision", "got-ocr2")

    print("\n=== FIXR-016: OCR Arm Comparison ===\n")
    print(f"Arm A: {arm_qwen.repo_id} ({arm_qwen.provider})")
    print(f"Arm B: {arm_got.repo_id}  ({arm_got.provider})")
    print()

    col = 22
    print(f"{'Case':<{col}} "
          f"{'A recall':>9} {'A inv':>6} {'A ms':>8}  "
          f"{'B recall':>9} {'B inv':>6} {'B ms':>8}  "
          f"Winner")
    print("-" * 80)

    rows_a, rows_b = [], []
    both_live = True

    for case in CASES:
        raw = case["file"].read_bytes()
        truth = case["truth"]

        try:
            text_a, ms_a, live_a = run_qwen_vlm(raw, "image/png")
        except Exception as exc:
            text_a, ms_a, live_a = f"[error: {exc}]", 0.0, False

        try:
            text_b, ms_b, live_b = run_got_ocr2(raw)
        except Exception as exc:
            text_b, ms_b, live_b = f"[error: {exc}]", 0.0, False

        if not (live_a and live_b):
            both_live = False

        ra = fact_recall(truth, text_a)
        rb = fact_recall(truth, text_b)
        ia = invented_ids(truth, text_a)
        ib = invented_ids(truth, text_b)

        rows_a.append((ra, ia, ms_a, live_a))
        rows_b.append((rb, ib, ms_b, live_b))

        # Winner: higher recall wins; tie-break on fewer invented ids; both offline = draw
        if not live_a and not live_b:
            winner = "—"
        elif not live_a:
            winner = "B"
        elif not live_b:
            winner = "A"
        elif ra > rb:
            winner = "A"
        elif rb > ra:
            winner = "B"
        elif ia < ib:
            winner = "A"
        elif ib < ia:
            winner = "B"
        else:
            winner = "draw"

        ra_str = f"{ra:.2f}" if live_a else " stub"
        ia_str = f"{ia:6}" if live_a else "  stub"
        ma_str = f"{ms_a:.0f}" if live_a else "    —"
        rb_str = f"{rb:.2f}" if live_b else " stub"
        ib_str = f"{ib:6}" if live_b else "  stub"
        mb_str = f"{ms_b:.0f}" if live_b else "    —"
        print(f"{case['name']:<{col}} "
              f"{ra_str:>9} {ia_str:>6} {ma_str:>8}  "
              f"{rb_str:>9} {ib_str:>6} {mb_str:>8}  "
              f"{winner}")

    print("-" * 80)

    # Aggregate
    live_a_any = any(r[3] for r in rows_a)
    live_b_any = any(r[3] for r in rows_b)

    def avg(vals, live_flag_idx):
        live = [v for v in vals if v[live_flag_idx]]
        if not live:
            return None, None
        return (
            sum(v[0] for v in live) / len(live),
            sum(v[1] for v in live) / len(live),   # avg invented_ids (float ok for display)
        )

    avg_ra, avg_ia = avg(rows_a, 3)
    avg_rb, avg_ib = avg(rows_b, 3)

    ra_str = f"{avg_ra:.2f}" if avg_ra is not None else "offline"
    ia_str = f"{avg_ia:.1f}" if avg_ia is not None else "offline"
    rb_str = f"{avg_rb:.2f}" if avg_rb is not None else "offline"
    ib_str = f"{avg_ib:.1f}" if avg_ib is not None else "offline"

    print(f"{'AVERAGE':<{col}} "
          f"{ra_str:>9} {ia_str:>6} {'':>8}  "
          f"{rb_str:>9} {ib_str:>6} {'':>8}")
    print()

    if not both_live:
        print("* offline stub — arm was not available; metrics reflect only live runs.")
        print()

    # Headline
    if avg_ra is not None and avg_rb is not None:
        if avg_ra > avg_rb:
            headline = f"Qwen2.5-VL wins on fact recall ({avg_ra:.2f} vs {avg_rb:.2f})"
        elif avg_rb > avg_ra:
            headline = f"GOT-OCR2_0 wins on fact recall ({avg_rb:.2f} vs {avg_ra:.2f})"
        else:
            headline = f"Tied on fact recall ({avg_ra:.2f})"
        print(f"Result: {headline}")
    elif avg_ra is not None:
        print(f"Partial result (Arm B offline): "
              f"Qwen2.5-VL — recall {avg_ra:.2f}, invented_ids {avg_ia:.1f}")
        print("GOT-OCR2_0 requires a CUDA GPU; re-run on a GPU machine for the full comparison.")
    elif avg_rb is not None:
        print(f"Partial result (Arm A offline): "
              f"GOT-OCR2_0 — recall {avg_rb:.2f}, invented_ids {avg_ib:.1f}")
        print("Qwen2.5-VL requires ollama + model pull; re-run when daemon is available.")
    else:
        print("Result: both arms offline — pull Qwen2.5-VL and add a GPU for GOT-OCR2_0.")

    print()
    return both_live


if __name__ == "__main__":
    ok = run_comparison()
    sys.exit(0 if ok else 1)
