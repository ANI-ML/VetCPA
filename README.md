# VetCPA

## ⬇️ Download & install

| Platform | Download | Size |
| --- | --- | --- |
| 🍎 **macOS** (Intel or Apple Silicon) | [**VetCPA-0.1.0.dmg**](https://github.com/ANI-ML/VetCPA/releases/download/v0.1.0/VetCPA-0.1.0.dmg) | ~338 MB |
| 🪟 **Windows 10 / 11** (64-bit) | [**VetCPA-windows-0.1.0.zip**](https://github.com/ANI-ML/VetCPA/releases/download/v0.1.0/VetCPA-windows-0.1.0.zip) | ~315 MB |

See [all releases](https://github.com/ANI-ML/VetCPA/releases) · [detailed install steps](#install) · [uninstall](#uninstall)

> **First launch:** VetCPA downloads ~1.5 GB of OCR models from HuggingFace. A progress bar shows the download — you'll know it's working. Every launch after that is fully offline.

---

**Bank-statement extraction for accountants — runs entirely on the user's own device.**

VetCPA turns bank and credit-card statements (PDFs, scans, or phone photos) into a single clean CSV the accountant can hand to a ledger or spreadsheet. Every row is tagged with which statement it came from and which parser produced it, so corrections are easy to spot and easy to make.

Built by [ANI.ML Health](https://animl.health) as a VetCPA pilot.

---

## Highlights

| | |
| :-- | :-- |
| 🖥  **Local-only** | No cloud APIs, no LLM calls, no telemetry. Your PDFs never leave the machine. |
| 📄  **Wide input support** | PDFs (digital-born *and* scanned), JPG, JPEG, PNG, TIFF, BMP, and HEIC/HEIF (iPhone photos). |
| 🏦  **Multi-bank from day one** | A Scotiabank Passport Visa parser ships as a reference; an always-on generic fallback handles every other bank. |
| ✏️  **In-app corrections** | Edit rows in the browser; the app learns from corrections via a local SQLite feedback log. |
| 📊  **Grouped output** | Per-statement title + account type lead every row; sorted so one CSV handles many statements cleanly. |
| 🌓  **Light + dark mode** | ANI.ML orange + VetCPA green. Follows system preference, togglable. |

---

## Canonical output schema

Every CSV VetCPA produces has these eight columns, in this order:

| Column | Description |
| --- | --- |
| `StatementTitle` | User-provided title (defaults to the filename). Rows with the same title group together. |
| `AccountType` | `visa` / `mastercard` / `amex` / `chequing` / `savings` / `other`. Auto-detected or user-picked from a dropdown. |
| `Date` | Transaction date, ISO `YYYY-MM-DD`. |
| `Amount` | Signed decimal; **negative = payment/credit**. |
| `Payee` | Cleaned merchant name (best-effort). |
| `Description` | Full transaction description, including any FX sub-line. |
| `Reference` | Bank-supplied reference number, if available. |
| `CheckNumber` | Cheque number, if available. |

Rows are sorted by `(StatementTitle, Date)`. Optional audit columns `source_bank` and `source_file` can be added with `--include-source`.

---

## Install

### 🍎 macOS — download and drag

1. **[Download `VetCPA-<version>.dmg`](https://github.com/ANI-ML/VetCPA/releases/latest)** from the Releases page.
2. **Double-click the `.dmg`** to open it. A Finder window pops up with the **VetCPA** app and an **Applications** shortcut side by side.
3. **Drag VetCPA into Applications.** Eject the disk image.
4. **Launch from Applications or Launchpad.** Your browser opens automatically to the VetCPA UI.

> **First time only:** macOS will say "VetCPA cannot be opened because the developer cannot be verified." **Right-click VetCPA → Open → Open.** Do this once; every later launch is a normal double-click.

That's it. Start dragging PDFs or phone photos onto the drop zone.

### 🪟 Windows — download and run

1. **[Download `VetCPA-windows-<version>.zip`](https://github.com/ANI-ML/VetCPA/releases/latest)** from the Releases page.
2. **Right-click the zip → Extract All…** (or use your preferred unzip tool). You'll get a `VetCPA\` folder containing `VetCPA.exe` plus support files.
3. **Move the `VetCPA\` folder** somewhere permanent (e.g. `C:\Users\<you>\Apps\VetCPA`).
4. **Double-click `VetCPA.exe`.** Your browser opens automatically to the VetCPA UI.

> **First time only:** Windows SmartScreen will warn about an unrecognised app. Click **More info → Run anyway**. Later launches are unprompted.

### First-run download (both platforms)

On first use, VetCPA downloads ~1.5 GB of OCR models from HuggingFace. This takes a few minutes and happens **once per machine**. You'll see a progress bar at the top of the UI the entire time — no guessing whether the app is frozen. Every launch after that is fully offline.

### 🐳 Docker — for devs, team servers, EC2

If you're running VetCPA on a shared machine or prefer a container:

```bash
git clone https://github.com/ANI-ML/VetCPA.git
cd VetCPA/pdf_to_csv
docker compose up
```

Open **http://localhost:8000**. Shut down with `Ctrl-C` or `docker compose down`.

What persists between restarts:
- `./data/feedback.db` — the accountant's corrections (host-mounted)
- `vetcpa-models` named volume — the downloaded OCR models (~2 GB, one-time)

EC2 sizing floor: **t3.medium** (2 vCPU, 4 GB RAM).

---

## Uninstall

Cleanly removes VetCPA **and** the ~2 GB of OCR models it downloaded. Everything shows what it's about to delete and asks for confirmation first.

### 🍎 macOS

Paste this in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/ANI-ML/VetCPA/main/pdf_to_csv/scripts/uninstall_macos.sh | bash
```

What it removes:

| Path | What it is |
| --- | --- |
| `/Applications/VetCPA.app` | the app |
| `~/Library/Application Support/VetCPA/` | feedback database |
| `~/.cache/docling/` | OCR models (~1.5 GB) |

What it doesn't touch: `~/.cache/huggingface/` (shared with other HF apps) and the `.dmg` you originally downloaded.

### 🪟 Windows

Paste this in PowerShell:

```powershell
iwr https://raw.githubusercontent.com/ANI-ML/VetCPA/main/pdf_to_csv/scripts/uninstall_windows.ps1 -OutFile uninstall.ps1 ; powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 ; Remove-Item uninstall.ps1
```

What it removes:

| Path | What it is |
| --- | --- |
| `%LOCALAPPDATA%\VetCPA\` | feedback database |
| `%USERPROFILE%\.cache\docling\` | OCR models (~1.5 GB) |

Then manually delete the extracted `VetCPA\` folder (wherever you unzipped it). Or run the script with `-AppPath '<path>'` to have it do that too:

```powershell
.\uninstall.ps1 -AppPath 'C:\Users\<you>\Apps\VetCPA'
```

Both scripts accept `-y` / `-Yes` to skip the confirmation prompt for scripted use.

---

## Building the installer yourself

Most people should just download the DMG from Releases. If you want to **build a fresh `.app` / `.dmg` from source** (e.g. to bundle different models, or to code-sign it):

### macOS

```bash
git clone https://github.com/ANI-ML/VetCPA.git
cd VetCPA/pdf_to_csv
make install-dev                    # one-time: builds .venv, installs all deps
./scripts/build_macos.sh            # produces dist/VetCPA.app  (15-25 min)
./scripts/make_dmg.sh               # wraps it as dist/VetCPA-0.1.0.dmg
```

- `VetCPA.app` is **~700 MB** without bundled models, **~3 GB** with them.
- The `.dmg` is a single file ideal for sharing over AirDrop / a shared drive.

### Fully offline build

To bake the OCR models into the `.app` / `.exe` so first launch doesn't hit the network:

```bash
# macOS — warm the model cache once, then rebuild with the flag
.venv/bin/pdf-to-csv inspect pdf_to_csv/samples/any-pdf.pdf
VETCPA_BUNDLE_MODELS=1 ./scripts/build_macos.sh
```

```powershell
# Windows — same pattern
.\.venv\Scripts\pdf-to-csv inspect pdf_to_csv\samples\any-pdf.pdf
$env:VETCPA_BUNDLE_MODELS = "1"
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

The resulting bundle is ~2 GB heavier but **never touches the network**.

---

## Using the app

1. **Drop files into the drop zone.** PDFs, scans, phone photos — anything in the accepted list. Drag in several at once if they belong to the same batch.
2. **Label each file.** Edit the title inline (defaults to filename). Pick an account type from the dropdown, or leave on *Auto-detect* and VetCPA guesses from the text. Images/HEIC auto-enable OCR.
3. **Click Extract transactions.** Shows a live spinner with elapsed seconds. First extraction loads Docling's models (~10–20 s); subsequent extractions are faster. When it completes you'll see:
    - Summary cards (files, rows, dedup delta, per-account-type totals)
    - Per-file status table (which parser ran, how many rows, any errors)
    - The full rows table with every transaction
4. **Edit anything wrong.** Every cell is an inline input (account-type is a dropdown). Modified rows tint orange; added rows tint green. Use `+ Add row` for missing transactions and `✕` to delete errant ones.
5. **Download CSV or Excel.** Files reflect the current edited state, not the raw pipeline output. The accountant hands these on.
6. **Save corrections.** Optional. Persists the diff (original vs corrected, plus action type) to `data/feedback.db`. Developers can mine this log later to tune the parsers.

---

## Supported inputs

- **Digital PDFs** — the main path. No OCR needed; runs fast.
- **Scanned PDFs** — enable OCR with the checkbox (`--ocr` on the CLI).
- **Images** (JPG, JPEG, PNG, TIFF, BMP) — Docling's image pipeline always uses OCR.
- **HEIC / HEIF** — iPhone defaults. VetCPA converts to JPEG in a temp directory before Docling sees it; the original `.heic` filename is preserved in the `source_file` audit column. Oversized images (24 MP+) are automatically downscaled to 2500 px long-edge before OCR to avoid Pillow's decompression-bomb guard.

---

## How multiple banks are handled

VetCPA ships with an **ordered parser registry** in [`src/pdf_to_csv/pipeline.py`](pdf_to_csv/src/pdf_to_csv/pipeline.py):

```python
PARSER_REGISTRY = [
    ScotiabankPassportVisaParser(),   # high-fidelity, bank-specific
    GenericTableParser(),             # universal fallback, always last
]
```

For each uploaded PDF/image:

1. Docling extracts every table in the document.
2. `detect_bank_parser()` walks the registry top to bottom. The first parser whose `is_match()` returns True wins.
3. That parser emits canonical `TransactionRow` objects, tagged with `source_bank` so the accountant can see which rows were high-fidelity vs fallback.

**Bank-specific parsers always win.** `GenericTableParser` never gets a look-in when a dedicated parser claims the document — but when nothing else matches (unknown bank, weird layout), it takes a best-effort swing using cell-content heuristics rather than headers, so **every PDF produces a usable CSV**.

### Adding a new bank parser

When a bank shows up often enough to warrant bespoke logic:

1. Create `src/pdf_to_csv/parsers/<bank>.py` with a `BaseParser` subclass. Implement:
    - `is_match(parsed: ParsedPDF) -> bool` — a unique text keyword plus a table that matches your bank's header shape.
    - `extract_transactions(parsed: ParsedPDF) -> list[TransactionRow]` — normalise rows to the canonical schema.
2. Insert the new parser **before** `GenericTableParser()` in `PARSER_REGISTRY`.
3. Add `tests/test_<bank>_parser.py` — use [`tests/test_scotiabank_parser.py`](pdf_to_csv/tests/test_scotiabank_parser.py) as the template: unit tests for each pure helper plus one end-to-end test built from a synthetic `ParsedPDF` mirroring a real statement.

The `GenericTableParser` stays in place — it's the safety net for every long-tail bank that doesn't yet have a named parser.

---

## The feedback loop

The pipeline is heuristic. OCR mis-aligns columns on phone photos; sign conventions vary between banks; even named parsers have blind spots until they're polished against real statements. Accountants fix these rows in the browser, and VetCPA captures those fixes:

- Every edit/add/delete is recorded as a `FeedbackRecord` in a local SQLite DB (`./data/feedback.db` by default, or wherever `PDF_TO_CSV_FEEDBACK_DB` points).
- Developers inspect the log via CLI:

```bash
pdf-to-csv feedback count                   # total records
pdf-to-csv feedback list --limit 20         # 20 most recent corrections
pdf-to-csv feedback export --out fb.json    # full dump as JSON for analysis
```

- Patterns in the log tell you where the next named parser should go, or which heuristic in `GenericTableParser` needs tightening.

---

## Developer reference

### Install

```bash
brew install python@3.11          # or your package manager
cd pdf_to_csv
make install-dev                  # creates .venv, installs package + dev deps
```

For the desktop-bundle build toolchain as well:

```bash
.venv/bin/pip install -e ".[dev,bundle]"
```

### Project layout

```
pdf_to_csv/
├── pyproject.toml                # setuptools + deps + CLI entry point
├── Dockerfile                    # Option B: runnable container
├── docker-compose.yml            # one-liner local deploy
├── VetCPA.spec                   # Option A: PyInstaller bundle spec
├── scripts/
│   ├── build_macos.sh            # builds dist/VetCPA.app
│   ├── build_windows.ps1         # builds dist/VetCPA/VetCPA.exe
│   └── make_dmg.sh               # wraps the .app as a .dmg
├── src/pdf_to_csv/
│   ├── __init__.py
│   ├── config.py                 # canonical output-schema column order
│   ├── models.py                 # TransactionRow (pydantic)
│   ├── account_type.py           # AccountType enum + text auto-detection
│   ├── ingest.py                 # file-type validation + HEIC/large-image normalisation
│   ├── docling_client.py         # thin wrapper around Docling
│   ├── parsers/
│   │   ├── base_parser.py        # abstract BaseParser
│   │   ├── scotiabank_passport_visa.py
│   │   └── generic_table.py      # universal fallback
│   ├── pipeline.py               # ordered registry, batch + dedup
│   ├── feedback_store.py         # SQLite-backed feedback log
│   ├── cli.py                    # typer CLI (inspect, extract, feedback)
│   ├── api.py                    # FastAPI (/, /extract, /export, /feedback)
│   ├── launcher.py               # PyInstaller entry point (desktop-bundle only)
│   └── static/index.html         # single-page drag-and-drop UI
├── tests/                        # 148 tests, <2s, no network
└── samples/                      # gitignored — drop sanitized PDFs here
```

### Common commands

| Command | Purpose |
| --- | --- |
| `make install-dev` | venv + package + dev deps |
| `make test` | full test suite (148 tests) |
| `make run-api` | uvicorn with `--reload` for iteration |
| `make run-cli ARGS="extract samples/foo.pdf --out out.csv"` | CLI through the venv |
| `make lint` / `make format` | ruff check / format |
| `./scripts/build_macos.sh` | build `dist/VetCPA.app` |
| `./scripts/make_dmg.sh` | wrap it as a DMG |
| `docker compose up` | launch via Docker on port 8000 |

### CLI

```bash
pdf-to-csv inspect samples/statement.pdf        # print Docling's extracted tables
pdf-to-csv inspect samples/statement.heic --ocr # same, forcing OCR

pdf-to-csv extract samples/april.pdf samples/may.pdf \
    --out out/transactions.csv \
    --excel out/transactions.xlsx

pdf-to-csv feedback {list,count,export}         # inspect the correction log
```

Flags for `extract`: `--ocr`, `--dedupe/--no-dedupe`, `--include-source`, `--excel <path>`.

### HTTP API (direct access)

`POST /extract` — multipart form with one or more `files`, optional parallel `titles` and `account_types`. Query params: `format=json|csv|excel`, `include_source`, `dedupe`, `ocr`.

```bash
curl -X POST http://localhost:8000/extract \
     -F "files=@samples/april.pdf" \
     -F "titles=March Visa" \
     -F "account_types=visa"
```

Other endpoints: `POST /export` (downloadable CSV/Excel from arbitrary row lists), `POST /feedback` + `GET /feedback` (correction log), `GET /health`.

See `http://localhost:8000/docs` for Swagger UI.

---

## Known limitations

- **Docling sometimes merges an interest/fee row into a subtotal** on Scotiabank layouts. Spot-check the last few rows against the statement's `SUB-TOTAL` line.
- **OCR on phone photos occasionally mis-aligns columns** (putting a debit value in the credit column, or vice-versa). You'll see this as an insurance-payment-looking row with a positive sign when it should be negative. Fix it inline; the correction persists to the feedback log.
- **Rows tagged `source_bank=generic_table` should always be reviewed.** The generic parser has no bank-specific knowledge; it extracts what it can using cell-content regexes and trusts the reader to verify.
- **Docling's model download is ~2 GB** and happens on first launch (or at build time if `VETCPA_BUNDLE_MODELS=1`). After that, VetCPA is fully offline.

---

## Privacy and data handling

**This tool does not make LLM calls. It does not send your data to any third-party service.**

A grep for `(openai|anthropic|claude|gemini|googleapi|api.key|cdn\.)` over the source tree returns zero hits. The only network event the application makes, **on the machine where it's installed**, is a one-time download of Docling's open-source model weights from HuggingFace Hub on first extraction. That download is model weights flowing **to** the device — no user data flows **out**.

For air-gapped operation:

- Use `VETCPA_BUNDLE_MODELS=1` at build time to bake the weights into the bundle.
- Or set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` at runtime after the first download, which makes the HuggingFace client refuse any further network access.

The feedback SQLite store lives on the local filesystem and never leaves the device.

---

## Acknowledgements

VetCPA is built on excellent open-source work:

- **[Docling](https://github.com/DS4SD/docling)** (IBM/DS4SD) for layout-aware table extraction.
- **FastAPI** + **uvicorn** for the HTTP layer.
- **pandas**, **openpyxl**, **pydantic**, **typer**, **pillow-heif** for the rest of the plumbing.

© 2025 ANI.ML Health.
