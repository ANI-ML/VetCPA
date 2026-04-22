"""Command-line interface.

Two subcommands:

    pdf-to-csv inspect <pdf>                        Print Docling-extracted tables
    pdf-to-csv extract <pdf>... --out out.csv       Run the full pipeline -> CSV

`extract` also accepts `--excel <path>` for an Excel workbook, `--ocr` for
scanned statements, and `--include-source` to add `source_bank`/`source_file`
columns for auditability.
"""
from __future__ import annotations

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
            "\nRun `pdf-to-csv <cmd> --help` for details."
        )


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    pdf: Path = typer.Argument(..., exists=True, readable=True, help="PDF file to inspect."),
    ocr: bool = typer.Option(
        False, "--ocr/--no-ocr", help="Enable Docling OCR (needed for scanned statements)."
    ),
    rows: int = typer.Option(
        3, "--rows", "-n", min=0, help="Number of body rows to preview per table."
    ),
) -> None:
    """Print every table Docling extracts from a PDF, with headers and a preview."""
    from pdf_to_csv.docling_client import parse_pdf_to_tables

    typer.echo(f"Parsing: {pdf}")
    tables = parse_pdf_to_tables(pdf, do_ocr=ocr)

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
    pdfs: list[Path] = typer.Argument(
        ..., exists=True, readable=True, help="One or more PDF statements to process."
    ),
    out: Path = typer.Option(..., "--out", "-o", help="Output CSV path."),
    excel: Path = typer.Option(
        None, "--excel", help="Optional Excel (.xlsx) output path.",
    ),
    ocr: bool = typer.Option(
        False, "--ocr/--no-ocr", help="Enable Docling OCR (for scanned statements)."
    ),
    dedupe: bool = typer.Option(
        True,
        "--dedupe/--no-dedupe",
        help="Drop rows with identical (Date, Amount, Description) across PDFs.",
    ),
    include_source: bool = typer.Option(
        False,
        "--include-source/--no-include-source",
        help="Add source_bank and source_file columns to the output.",
    ),
) -> None:
    """Extract transactions from one or more PDFs into a unified CSV."""
    # Lazy imports — keep --help fast, keep tests cheap.
    from pdf_to_csv.docling_client import build_converter
    from pdf_to_csv.pipeline import extract_transactions_from_many

    typer.echo(f"Processing {len(pdfs)} PDF(s)...")

    # Build the Docling converter once and reuse across every PDF — model load
    # is the expensive bit.
    converter = build_converter(do_ocr=ocr)

    df, results = extract_transactions_from_many(
        pdfs,
        dedupe=dedupe,
        include_source=include_source,
        converter=converter,
    )

    # Per-PDF progress with the parser that claimed each one.
    for r in results:
        rel = r.pdf_path.name
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
