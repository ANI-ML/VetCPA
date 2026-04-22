"""Command-line interface.

Subcommands:

    pdf-to-csv inspect <file>                         Print Docling-extracted tables
    pdf-to-csv extract <file>... --out out.csv        Run the full pipeline -> CSV
    pdf-to-csv feedback <list|export|count>           Inspect the feedback log

Both `inspect` and `extract` accept PDFs and images (JPG/JPEG/PNG/TIF/TIFF/
BMP/HEIC/HEIF). HEIC/HEIF files are transparently converted to JPEG before
Docling sees them.

`extract` also accepts `--excel <path>` for an Excel workbook, `--ocr` for
scanned statements, and `--include-source` to add `source_bank`/`source_file`
columns for auditability.
"""
from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="Convert bank/credit-card statement PDFs into a unified CSV/Excel.",
)


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(
            "pdf-to-csv: run with a subcommand.\n"
            "  inspect <pdf>                  Print every table Docling extracts.\n"
            "  extract <pdf>... --out <csv>   Run pipeline, write CSV (+optional Excel).\n"
            "  feedback list | export | count Inspect the feedback log.\n"
            "\nRun `pdf-to-csv <cmd> --help` for details."
        )


feedback_app = typer.Typer(
    add_completion=False,
    help="Inspect the user-correction feedback log (SQLite-backed).",
)
app.add_typer(feedback_app, name="feedback")


@feedback_app.command("list")
def feedback_list(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=1000,
                              help="Max records to show (most recent first)."),
) -> None:
    """Print the most recent feedback records, one per line."""
    from pdf_to_csv.feedback_store import FeedbackStore, default_db_path

    store = FeedbackStore(default_db_path())
    records = store.list_all(limit=limit)
    if not records:
        typer.echo("No feedback recorded yet.")
        return
    typer.echo(f"{len(records)} record(s), newest first:\n")
    for r in records:
        when = r.created_at.isoformat(timespec="seconds")
        title = r.statement_title or "—"
        src = r.source_file or "—"
        typer.echo(f"  [{r.id}] {when} {r.action:<6} {title} / {src}")


@feedback_app.command("count")
def feedback_count_cmd() -> None:
    """Print the total number of feedback records."""
    from pdf_to_csv.feedback_store import FeedbackStore, default_db_path
    typer.echo(str(FeedbackStore(default_db_path()).count()))


@feedback_app.command("export")
def feedback_export(
    out: Path = typer.Option(..., "--out", "-o", help="Destination JSON file."),
    limit: int = typer.Option(0, "--limit", "-n", min=0,
                              help="Max records to export (0 = all)."),
) -> None:
    """Dump feedback records as a JSON array to `out`.

    This is the pragmatic way to get the full dataset to a developer for
    analysis — they can open it in pandas / jq / whatever.
    """
    import json
    from pdf_to_csv.feedback_store import FeedbackStore, default_db_path

    store = FeedbackStore(default_db_path())
    records = store.list_all(limit=limit or None)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(
            [r.model_dump(mode="json") for r in records],
            f, ensure_ascii=False, indent=2,
        )
    typer.echo(f"Wrote {len(records)} record(s) to {out}")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    pdf: Path = typer.Argument(
        ..., exists=True, readable=True,
        help="File to inspect (PDF or image: JPG / PNG / HEIC / ...).",
    ),
    ocr: bool = typer.Option(
        False, "--ocr/--no-ocr",
        help="Enable Docling OCR for PDFs (image inputs always use OCR).",
    ),
    rows: int = typer.Option(
        3, "--rows", "-n", min=0, help="Number of body rows to preview per table."
    ),
) -> None:
    """Print every table Docling extracts from a statement, with headers and a preview."""
    from pdf_to_csv.docling_client import parse_pdf_to_tables
    from pdf_to_csv.ingest import (
        IngestError, accepted_types_label, is_supported, normalize_for_docling,
    )

    if not is_supported(pdf):
        typer.secho(
            f"Unsupported file type: {pdf} (accepted: {accepted_types_label()})",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    # HEIC → JPEG + oversized-image downscale if needed; no-op for everything else.
    with tempfile.TemporaryDirectory(prefix="pdf_to_csv_inspect_") as td:
        try:
            docling_path = normalize_for_docling(pdf, work_dir=Path(td))
        except IngestError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=2) from exc
        typer.echo(f"Parsing: {pdf}")
        tables = parse_pdf_to_tables(docling_path, do_ocr=ocr)

    if not tables:
        typer.echo("No tables found.")
        raise typer.Exit(code=0)

    typer.echo(f"Found {len(tables)} table(s).\n")
    for i, t in enumerate(tables, start=1):
        page = t.page_number if t.page_number is not None else "?"
        n_rows, n_cols = t.shape
        typer.echo(f"── Table {i} (page {page}) — {n_rows} rows × {n_cols} cols")
        typer.echo("  Headers: " + " | ".join(t.headers))
        for r in t.rows[:rows]:
            typer.echo("    " + " | ".join(str(c) for c in r))
        if len(t.rows) > rows:
            typer.echo(f"    ... ({len(t.rows) - rows} more rows)")
        typer.echo("")


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


@app.command()
def extract(
    files: list[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more statement files (PDF / JPG / PNG / HEIC / ...) to process.",
    ),
    out: Path = typer.Option(..., "--out", "-o", help="Output CSV path."),
    excel: Path = typer.Option(
        None, "--excel", help="Optional Excel (.xlsx) output path.",
    ),
    ocr: bool = typer.Option(
        False, "--ocr/--no-ocr",
        help="Enable Docling OCR for PDFs (image inputs always use OCR).",
    ),
    dedupe: bool = typer.Option(
        True,
        "--dedupe/--no-dedupe",
        help="Drop rows with identical (Title, Date, Amount, Description) across files.",
    ),
    include_source: bool = typer.Option(
        False,
        "--include-source/--no-include-source",
        help="Add source_bank and source_file columns to the output.",
    ),
) -> None:
    """Extract transactions from one or more statements into a unified CSV."""
    # Lazy imports — keep --help fast, keep tests cheap.
    from pdf_to_csv.docling_client import build_converter
    from pdf_to_csv.ingest import (
        IngestError, accepted_types_label, is_supported, normalize_for_docling,
    )
    from pdf_to_csv.pipeline import PdfJob, extract_transactions_from_many

    # Validate extensions up-front — fail fast before building Docling.
    bad = [p for p in files if not is_supported(p)]
    if bad:
        for p in bad:
            typer.secho(
                f"Unsupported file type: {p} (accepted: {accepted_types_label()})",
                fg=typer.colors.RED,
            )
        raise typer.Exit(code=2)

    typer.echo(f"Processing {len(files)} file(s)...")

    # Build the Docling converter once and reuse across every file — model load
    # is the expensive bit.
    converter = build_converter(do_ocr=ocr)

    # Temp dir for HEIC → JPEG conversions. `normalize_for_docling` is a no-op
    # for non-HEIC inputs so this dir stays empty in the common case.
    with tempfile.TemporaryDirectory(prefix="pdf_to_csv_cli_") as td:
        work_dir = Path(td)
        jobs: list[PdfJob] = []
        for f in files:
            try:
                docling_path = normalize_for_docling(f, work_dir=work_dir)
            except IngestError as exc:
                typer.secho(f"  {f.name}: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=2) from exc
            jobs.append(PdfJob(path=docling_path, original_filename=f.name))

        df, results = extract_transactions_from_many(
            jobs,
            dedupe=dedupe,
            include_source=include_source,
            converter=converter,
        )

    # Per-file progress with the parser that claimed each one.
    for r in results:
        rel = r.display_name or r.pdf_path.name
        if r.error:
            typer.secho(f"  {rel}: FAILED — {r.error}", fg=typer.colors.RED)
        else:
            parser_label = r.parser_name or "?"
            typer.echo(f"  {rel}: {len(r.transactions)} rows ({parser_label})")

    _write_outputs(df, out, excel)
    _print_summary(results, total_after_dedup=len(df), dedupe=dedupe)

    # Non-zero exit if anything failed to parse, so shell scripts can branch on it.
    if any(r.error for r in results):
        raise typer.Exit(code=1)


def _write_outputs(df, out: Path, excel: Path | None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8")
    typer.echo(f"\nWrote CSV:   {out}")

    if excel is not None:
        excel.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(excel, index=False, engine="openpyxl")
        typer.echo(f"Wrote Excel: {excel}")


def _print_summary(results, *, total_after_dedup: int, dedupe: bool) -> None:
    total_extracted = sum(len(r.transactions) for r in results)
    n_failed = sum(1 for r in results if r.error)

    by_parser = Counter(
        t.source_bank or "unknown"
        for r in results
        for t in r.transactions
    )

    typer.echo("\nSummary")
    typer.echo(f"  PDFs processed : {len(results)} ({n_failed} failed)")
    if dedupe and total_after_dedup != total_extracted:
        typer.echo(
            f"  Rows extracted : {total_extracted} → {total_after_dedup} after dedup"
        )
    else:
        typer.echo(f"  Rows extracted : {total_extracted}")
    for name, count in sorted(by_parser.items()):
        typer.echo(f"    {name}: {count}")


if __name__ == "__main__":
    app()
