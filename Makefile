PY := .venv/bin/python
PIP := .venv/bin/pip

PORT ?= 8000

.PHONY: help install refresh refresh-llm build test doctor list serve dev fixtures clean

help:
	@echo "make install     create .venv and install dependencies"
	@echo "make refresh     scrape all enabled sources into data/events.db"
	@echo "make refresh-llm same, plus Claude tagging for ambiguous events"
	@echo "make build       write site/dist (index.html, events.json, *.ics)"
	@echo "make test        run the golden-file test suite"
	@echo "make doctor      check sources for silent breakage"
	@echo "make list        print upcoming events in the terminal"
	@echo "make dev         live local site: rebuilds on change, reloads itself"
	@echo "make serve       build once and serve it statically"
	@echo "make fixtures    re-capture test fixtures from the live sites"
	@echo ""
	@echo "  PORT=8001 make dev            use a different port"
	@echo "  make dev REFRESH=30           also re-scrape the venues every 30 min"

.venv:
	python3 -m venv .venv
	$(PIP) install --upgrade pip

install: .venv
	$(PIP) install -r requirements.txt
	$(PIP) install pytest anthropic pypdf

refresh:
	$(PY) -m delhi_events.cli refresh

refresh-llm:
	$(PY) -m delhi_events.cli refresh --llm

build:
	$(PY) -m delhi_events.cli build

test:
	$(PY) -m pytest tests/ -q

doctor:
	$(PY) -m delhi_events.cli doctor

list:
	$(PY) -m delhi_events.cli list

REFRESH ?= 0

dev:
	$(PY) scripts/dev_server.py --port $(PORT) --refresh-every $(REFRESH)

serve: build
	@echo "http://localhost:$(PORT)/"
	@cd site/dist && $(abspath $(PY)) -m http.server $(PORT)

fixtures:
	$(PY) scripts/capture_fixtures.py

clean:
	rm -rf .cache site/dist __pycache__ .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
