.PHONY: install install-dev run-api run-cli test lint format clean

VENV ?= .venv
PY   ?= $(VENV)/bin/python
PIP  ?= $(VENV)/bin/pip

$(VENV)/bin/activate:
	python3.11 -m venv $(VENV)

install: $(VENV)/bin/activate
	$(PIP) install --upgrade pip
	$(PIP) install -e .

install-dev: $(VENV)/bin/activate
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

run-api:
	$(VENV)/bin/uvicorn pdf_to_csv.api:app --reload --host 0.0.0.0 --port 8000

# Usage: make run-cli ARGS="samples/statement.pdf --out out.csv"
run-cli:
	$(VENV)/bin/pdf-to-csv $(ARGS)

test:
	$(VENV)/bin/pytest -v

lint:
	$(VENV)/bin/ruff check src tests

format:
	$(VENV)/bin/ruff format src tests

clean:
	rm -rf $(VENV) build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
