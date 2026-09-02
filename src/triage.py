"""The triage intake CLI (FIXR-005): accept any of the three input kinds, print the evidence.

    uv run python -m src.triage --text "disk full on prod-3" \
                                --audio report.mp3 \
                                --screenshot error.png

Any subset of the three flags may be given; each one that is present becomes one evidence record.
The response is JSON on stdout, and its top-level `evidence_ids` is the list this run used — that
list is the phase gate. A one-line-per-record summary goes to stderr so a human watching the run
can read it without parsing the JSON, and stdout stays pure JSON so a script can.

This is the "one path" the ticket asks for: the three routes converge in `src/ingest.py`, and this
file only parses flags and shapes the response. `run()` is the same entry `make demo` and the tests
call, so the number the gate re-runs is produced by the exact code a test exercised.
"""
import argparse
import json
import sys

from src import config, ingest, telemetry

# The diagnostic arms (FIXR-008). Both answer the same case through the same run(); they differ in
# exactly one leg — whether a screenshot is read by the vision VLM — so the difference between their
# answers is the vision contribution and nothing else.
#
#   vision      the full multimodal path (FIXR-005): a screenshot is read into text by the VLM.
#   text-only   the ablation, and the local fallback arm: the screenshot is received and gets its
#               stable evidence id, but the VLM is never called and its content is a labelled
#               'vision suppressed' placeholder. Text and audio are untouched — audio is
#               speech-to-TEXT, not vision — so the only variable removed is the read of the pixels.
#
# 'local' in the ticket names what this arm needs: no vision model, no ollama daemon, no NIM key. It
# is the leg that always runs, and the baseline every vision number is measured against.
ARMS = ("vision", "text-only")
DEFAULT_ARM = "vision"


def run(*, text=None, text_source="text:inline", audio=None, screenshot=None, arm=DEFAULT_ARM,
        stt_model=None, vision_model=None, turn_id=None):
    """-> the response dict for the given inputs. The one place the three paths are collected.

    `arm` selects a diagnostic arm (see ARMS). It is recorded in the response and changes exactly
    one thing: the text-only arm suppresses the screenshot read so the vision contribution can be
    isolated by diffing the two arms' answers on the same case.

    Records come out in a fixed order — text, audio, screenshot — so two runs of the same inputs and
    arm produce byte-identical JSON, which is what lets the gate compare output rather than eyeball
    it.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown triage arm {arm!r} — expected one of {ARMS}")
    turn_id = turn_id or telemetry.new_turn_id()
    records = []
    if text is not None:
        records.append(ingest.ingest_text(text, source=text_source))
    if audio is not None:
        records.append(ingest.ingest_audio(audio, turn_id=turn_id, model_id=stt_model))
    if screenshot is not None:
        records.append(ingest.ingest_screenshot(screenshot, turn_id=turn_id,
                                                 model_id=vision_model, read=(arm == "vision")))
    return {
        "turn_id": turn_id,
        "arm": arm,
        "evidence": [r.model_dump() for r in records],
        "evidence_ids": [r.id for r in records],
    }


def _summary_line(record):
    """One human-readable line for stderr. The origin (which arm produced the content) plus a flag
    when nothing live did — an offline stub or a suppressed read — is the fact worth surfacing."""
    flag = "" if record["live"] else "  [not live]"
    return f"  {record['kind']:<10} {record['id']}  ({record['origin']}){flag}"


def main(argv=None):
    config.utf8_console()
    parser = argparse.ArgumentParser(
        prog="python -m src.triage",
        description="Turn text, audio, and/or a screenshot into evidence records.")
    parser.add_argument("--text", metavar="STR", help="a typed note or pasted log line")
    parser.add_argument("--text-file", metavar="PATH", dest="text_file",
                        help="read the text input from a file (e.g. a saved log) instead of --text")
    parser.add_argument("--audio", metavar="PATH", help="an audio file to transcribe (whisper)")
    parser.add_argument("--screenshot", metavar="PATH", help="a screenshot to read (vision VLM)")
    parser.add_argument("--arm", choices=ARMS, default=DEFAULT_ARM,
                        help="which diagnostic arm answers the case; default %(default)s. "
                             "'vision' reads screenshots with the VLM; 'text-only' suppresses that "
                             "read to isolate the vision contribution (FIXR-008)")
    parser.add_argument("--stt", metavar="MODEL_ID", default=None,
                        help=f"STT arm; default {config.DEFAULT_STT.repo_id}")
    parser.add_argument("--vision", metavar="MODEL_ID", default=None,
                        help=f"vision arm; default {config.DEFAULT_VISION.repo_id}")
    args = parser.parse_args(argv)

    if args.text is not None and args.text_file is not None:
        parser.error("give --text or --text-file, not both")
    text, text_source = args.text, "text:inline"
    if args.text_file is not None:
        from pathlib import Path
        text = Path(args.text_file).read_text(encoding="utf-8")
        text_source = args.text_file

    if text is None and args.audio is None and args.screenshot is None:
        parser.error("give at least one of --text / --text-file / --audio / --screenshot")

    response = run(text=text, text_source=text_source, audio=args.audio,
                   screenshot=args.screenshot, arm=args.arm, stt_model=args.stt,
                   vision_model=args.vision)

    print(f"triage {response['turn_id']} [{response['arm']} arm] — "
          f"{len(response['evidence'])} evidence record(s):", file=sys.stderr)
    for record in response["evidence"]:
        print(_summary_line(record), file=sys.stderr)
    print(f"  evidence_ids used: {response['evidence_ids']}", file=sys.stderr)

    print(json.dumps(response, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
