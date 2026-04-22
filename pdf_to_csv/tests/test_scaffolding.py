"""Smoke tests for the scaffolded project — confirms imports wire up cleanly."""
from __future__ import annotations


def test_package_imports() -> None:
    import pdf_to_csv
    from pdf_to_csv import api, cli, config, docling_client, models, pipeline  # noqa: F401
    from pdf_to_csv.parsers import base_parser  # noqa: F401
    from pdf_to_csv.parsers import scotiabank_passport_visa  # noqa: F401

    assert pdf_to_csv.__version__ == "0.1.4"


def test_settings_default_columns() -> None:
    from pdf_to_csv.config import load_settings

    s = load_settings()
    assert s.output_columns == (
        "StatementTitle",
        "AccountType",
        "Date",
        "Amount",
        "Payee",
        "Description",
        "Reference",
        "CheckNumber",
    )


def test_api_health_endpoint() -> None:
    from fastapi.testclient import TestClient

    from pdf_to_csv.api import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
