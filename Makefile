.PHONY: setup doctor test gate demo coach clean
.DEFAULT_GOAL := help

help:
	@echo "make setup   create the venv and install deps (uv)"
	@echo "make doctor  check every external dependency; non-zero if any is missing"
	@echo "make test    unit tests"
	@echo "make gate    run every phase gate in tests/gates/"
	@echo "make demo    run the thing end to end"
	@echo "make coach   serve the Day 1 concept primer at http://localhost:8000/fixr-day1.html"

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
	@echo "not implemented yet. make demo must run the system end to end from a clean clone."
	@exit 1

coach:
	@echo "Serving concept primer at http://localhost:8000/fixr-day1.html"
	@echo "Ctrl-C to stop."
	cd docs && python3 -m http.server 8000

clean:
	rm -rf .venv .pytest_cache **/__pycache__
