.PHONY: setup doctor test gate demo ablation clean
.DEFAULT_GOAL := help

help:
	@echo "make setup     create the venv and install deps (uv)"
	@echo "make doctor    check every external dependency; non-zero if any is missing"
	@echo "make test      unit tests"
	@echo "make gate      run every phase gate in tests/gates/"
	@echo "make demo      run the thing end to end"
	@echo "make ablation  run one case through both arms (vision vs text-only, FIXR-008)"

setup:
	@command -v uv >/dev/null || { echo "uv not installed: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	uv sync
	@test -f .env || { cp .env.example .env; echo "wrote .env from .env.example — fill it in"; }
	@echo "ok. next: make doctor"

doctor:
	uv run python -m src.doctor

test:
	uv run pytest tests -q --ignore=tests/gates

gate:
	@test -n "$$(ls tests/gates/*.py 2>/dev/null)" || { echo "no gates written yet — see tests/gates/README.md"; exit 1; }
	uv run pytest tests/gates -q

demo:
	@uv run python -m src.triage \
		--text-file demo/incident_note.txt \
		--audio tests/fixtures/casual_leave_question.mp3 \
		--screenshot demo/incident_screenshot.png

# FIXR-008: the same case through both arms. Same inputs, same evidence ids; the screenshot record
# is the only thing that differs — read by the VLM under `vision`, suppressed under `text-only`.
# That difference is the vision contribution, isolated. Text-only needs no key and no daemon.
ablation:
	@echo "== vision arm =="
	@uv run python -m src.triage --arm vision \
		--text-file demo/incident_note.txt \
		--screenshot demo/incident_screenshot.png
	@echo "== text-only arm =="
	@uv run python -m src.triage --arm text-only \
		--text-file demo/incident_note.txt \
		--screenshot demo/incident_screenshot.png

clean:
	rm -rf .venv .pytest_cache **/__pycache__
