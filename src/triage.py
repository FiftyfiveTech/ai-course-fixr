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


def run(*, text=None, text_source="text:inline", audio=None, screenshot=None, stt_model=None,
        vision_model=None, turn_id=None):
    """-> the response dict for the given inputs. The one place the three paths are collected.

    Records come out in a fixed order — text, audio, screenshot — so two runs of the same inputs
    produce byte-identical JSON, which is what lets the gate compare output rather than eyeball it.
    """
    turn_id = turn_id or telemetry.new_turn_id()
    records = []
    if text is not None:
        records.append(ingest.ingest_text(text, source=text_source))
    if audio is not None:
        records.append(ingest.ingest_audio(audio, turn_id=turn_id, model_id=stt_model))
    if screenshot is not None:
        records.append(ingest.ingest_screenshot(screenshot, turn_id=turn_id,
                                                 model_id=vision_model))
    return {
        "turn_id": turn_id,
        "evidence": [r.model_dump() for r in records],
        "evidence_ids": [r.id for r in records],
    }


def _summary_line(record):
    """One human-readable line for stderr. `live` vs the offline stub is the fact worth surfacing."""
    how = record["origin"] if record["live"] else "OFFLINE STUB"
    return f"  {record['kind']:<10} {record['id']}  ({how})"


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
                   screenshot=args.screenshot, stt_model=args.stt, vision_model=args.vision)

    print(f"triage {response['turn_id']} — {len(response['evidence'])} evidence record(s):",
          file=sys.stderr)
    for record in response["evidence"]:
        print(_summary_line(record), file=sys.stderr)
    print(f"  evidence_ids used: {response['evidence_ids']}", file=sys.stderr)

    print(json.dumps(response, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
