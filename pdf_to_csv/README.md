# pdf-to-csv

Convert bank and credit-card statement PDFs into a unified CSV/Excel file for an accountant.
Built as a pilot for VetCPA (ANIML).

Uses [Docling](https://github.com/DS4SD/docling) to extract tables from PDFs, then an ordered registry of bank-specific parsers (plus a universal fallback) normalizes each transaction into the canonical schema:

| Column      | Description                                                 |
| ----------- | ----------------------------------------------------------- |
| Date        | Transaction date, ISO `YYYY-MM-DD`                          |
| Amount      | Signed decimal; **negative = payment/credit**               |
| Payee       | Cleaned payee/merchant name                                 |
| Description | Full transaction description (including any FX sub-line)    |
| Reference   | Bank-supplied reference number, if available                |
| CheckNumber | Cheque number, if available                                 |

Three ways to use it — **CLI** for batch jobs, **drag-and-drop web UI** at `/`, and **JSON/CSV/Excel HTTP API** at `/extract`.

## Status

**v0.1.0 pilot — all 8 build steps complete.** 86 tests passing. Verified end-to-end against a real Scotiabank Passport Visa statement; see [Known limitations](#known-limitations) for the edge cases worth spot-checking.

## Prerequisites

- macOS (Mac Studio for dev; Linux for EC2 deploy).
- **Python 3.11+**. System Python on older macOS is 3.9; install a newer one via Homebrew:

```bash
brew install python@3.11
```

- ~2 GB free disk — Docling downloads ML models on first run.

## Install

Using the Makefile (creates `.venv`, installs package + dev deps in editable mode):

```bash
cd pdf_to_csv
make install-dev
```

Or the plain-venv equivalent:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## CLI

Two subcommands. Run `pdf-to-csv <cmd> --help` for full options.

### `inspect` — peek at what Docling extracts

Use this to see the raw tables Docling finds, before writing any parser logic:

```bash
.venv/bin/pdf-to-csv inspect samples/statement.pdf
.venv/bin/pdf-to-csv inspect samples/statement.pdf --ocr --rows 10
```

### `extract` — run the full pipeline to CSV (+ optional Excel)

```bash
.venv/bin/pdf-to-csv extract samples/april.pdf samples/may.pdf \
    --out out/transactions.csv \
    --excel out/transactions.xlsx
```

Flags:

| Flag                       | Default | Meaning                                                                 |
| -------------------------- | :-----: | ----------------------------------------------------------------------- |
| `--out <path>`             | _required_ | CSV output path.                                                     |
| `--excel <path>`           | off     | Also write an `.xlsx` workbook.                                         |
| `--ocr / --no-ocr`         | off     | Enable Docling OCR (for scanned PDFs).                                  |
| `--dedupe / --no-dedupe`   | on      | Drop rows with identical `(Date, Amount, Description)` across files.    |
| `--include-source`         | off     | Add `source_bank` + `source_file` columns for auditability.             |

Exit code is non-zero if any PDF failed to parse, so CI/shell scripts can branch on it.

## Web UI + HTTP API

```bash
make run-api
# -> http://localhost:8000/         drag-and-drop UI
# -> http://localhost:8000/docs     Swagger UI for /extract
# -> http://localhost:8000/health   liveness
```

Drop PDFs onto the page and hit **Extract transactions**. Results show up in a scrollable table; **Download CSV** and **Download Excel** buttons save the output.

Or call `/extract` directly:

```bash
curl -X POST http://localhost:8000/extract \
     -F "files=@samples/april.pdf" \
     -F "files=@samples/may.pdf" \
     -o transactions.json

curl -X POST "http://localhost:8000/extract?format=csv" \
     -F "files=@samples/april.pdf" \
     -o transactions.csv
```

Query params: `format=json|csv|excel`, `include_source=true|false`, `dedupe=true|false`, `ocr=true|false`.

## How the pipeline handles multiple banks

The pipeline is an **ordered parser registry** ([pipeline.py](src/pdf_to_csv/pipeline.py)):

```python
PARSER_REGISTRY = [
    ScotiabankPassportVisaParser(),   # high-fidelity, bank-specific
    GenericTableParser(),             # universal fallback, always last
]
```

For each PDF:

1. `parse_pdf()` runs Docling once to extract tables + markdown text.
2. `detect_bank_parser()` walks the registry top-to-bottom; the first parser whose `is_match()` returns True wins.
3. The winning parser emits canonical `TransactionRow` objects, tagged with `source_bank` so the accountant can see in the output which rows were high-fidelity vs. fallback.

**Bank-specific parsers always take precedence.** `GenericTableParser` is the *universal fallback* — it detects date/amount/description columns by cell-content regexes (not headers), so it works even on banks we've never seen. Rows it produces carry `source_bank="generic_table"` and should be spot-checked by the accountant.

## Adding a new bank parser

When a bank's statements show up often enough to warrant bespoke logic (sign conventions, FX handling, multi-line rows), promote it from the generic fallback to a named parser.

**1. Create the parser.** In [src/pdf_to_csv/parsers/](src/pdf_to_csv/parsers/), add `<bank>.py`:

```python
from pdf_to_csv.docling_client import ParsedPDF
from pdf_to_csv.models import TransactionRow
from pdf_to_csv.parsers.base_parser import BaseParser


class MyBankParser(BaseParser):
    name = "my_bank"

    def is_match(self, parsed: ParsedPDF) -> bool:
        # Unique keyword in the text AND a table that matches this bank's shape.
        if "My Bank" not in (parsed.text or ""):
            return False
        return any(self._looks_like_my_bank_table(t.headers) for t in parsed.tables)

    def extract_transactions(self, parsed: ParsedPDF) -> list[TransactionRow]:
        rows: list[TransactionRow] = []
        for table in parsed.tables:
            if not self._looks_like_my_bank_table(table.headers):
                continue
            # Parse the bank's rows into TransactionRow(Date=..., Amount=..., ...)
            # Tag each row: source_bank=self.name
        return rows

    @staticmethod
    def _looks_like_my_bank_table(headers: list[str]) -> bool:
        ...
```

**2. Register it.** Add it to [PARSER_REGISTRY](src/pdf_to_csv/pipeline.py) **before** `GenericTableParser()`:

```python
PARSER_REGISTRY = [
    ScotiabankPassportVisaParser(),
    MyBankParser(),
    GenericTableParser(),
]
```

**3. Test it.** Create `tests/test_my_bank_parser.py` using [test_scotiabank_parser.py](tests/test_scotiabank_parser.py) as the template. The pattern:

- Unit test each pure helper (header detection, amount parsing, date parsing, row classification).
- One end-to-end test that builds a synthetic `ParsedPDF` mirroring a real statement and asserts the extracted rows.

The `GenericTableParser` stays in place — it's insurance against the long tail of banks that don't yet have a named parser.

## Known limitations

Things to watch for when reviewing output, especially on the first run against a new bank:

- **Docling sometimes merges an interest or fee row into the subtotal row** on Scotiabank statements. In the April 2025 reference run, the final `INTEREST CHARGES-PURCHASE` line was absorbed into `SUB-TOTAL DEBITS`. Totals still reconcile, but that one row may need to be added manually. Spot-check the last few rows against the statement's `SUB-TOTAL` line.
- **FX sublines occasionally bleed into the wrong row's Description** when Docling's row-splitting mis-aligns across page breaks. The Amount and Date are still correct; only the Payee/Description for that one row is noisy. This happened to ref 022 in the reference run.
- **Rows tagged `source_bank="generic_table"` should be reviewed**, always. The generic parser doesn't know bank-specific conventions (sign flips, FX handling, etc.). Use `--include-source` to surface the tag in output.
- **Docling install is large** (~GB with ML model weights). Download happens on first `extract` call; cached thereafter.

## Deployment

### Local dev (Mac Studio)

`make run-api` is sufficient. Uvicorn with `--reload` restarts on code changes.

### AWS EC2 (production-ish pilot)

Minimum: t3.medium or larger (Docling is CPU-heavy, 4 GB RAM headroom recommended). Ubuntu 22.04 LTS works. Outline:

```bash
# On the instance
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
git clone <repo> && cd pdf_to_csv
python3.11 -m venv .venv
.venv/bin/pip install -e .

# Run via uvicorn (pilot) — swap for gunicorn + nginx for a real deploy.
.venv/bin/uvicorn pdf_to_csv.api:app --host 0.0.0.0 --port 8000
```

For a proper deploy: run `uvicorn` under `systemd`, front it with `nginx` for TLS, and bump the per-file upload cap in [api.py](src/pdf_to_csv/api.py) if statements routinely exceed 50 MB.

## Layout

```
pdf_to_csv/
├── pyproject.toml               # setuptools + deps
├── requirements.txt             # same deps for plain-venv setup
├── Makefile                     # install / run / test
├── README.md                    # this file
├── src/pdf_to_csv/
│   ├── __init__.py
│   ├── config.py                # canonical output schema
│   ├── models.py                # TransactionRow (pydantic)
│   ├── docling_client.py        # thin Docling wrapper + ParsedPDF
│   ├── parsers/
│   │   ├── base_parser.py       # abstract BaseParser
│   │   ├── scotiabank_passport_visa.py
│   │   └── generic_table.py     # universal fallback
│   ├── pipeline.py              # ordered registry, batch + dedup
│   ├── cli.py                   # typer CLI (inspect, extract)
│   ├── api.py                   # FastAPI (/, /extract, /health)
│   └── static/index.html        # drag-and-drop front end
├── tests/                       # 86 tests; pytest-only, no network
├── samples/                     # drop PDFs here (gitignored)
└── out/                         # CSV/Excel outputs (gitignored)
```

## Developer reference

| Command           | Purpose                                 |
| ----------------- | --------------------------------------- |
| `make install-dev`| venv + package + dev deps (pytest, ruff)|
| `make test`       | run the full test suite                 |
| `make run-api`    | uvicorn with `--reload`                 |
| `make run-cli ARGS="..."` | run the CLI through the venv    |
| `make lint`       | ruff check                              |
| `make format`     | ruff format                             |
| `make clean`      | nuke `.venv`, caches, build artifacts   |

Test expectations: `86 passed, 0 skipped` on a clean install. All tests run in <1s and don't hit the network; Docling is mocked via fixture-supplied `ExtractedTable` / `ParsedPDF` objects.
