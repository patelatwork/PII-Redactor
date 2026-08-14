"""HTTP surface tests.

Skipped when the ``service`` extra is not installed, so ``pytest`` still passes
on a rules-only install.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from service.app import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "PII Redaction Service" in response.text


def test_analyze_reports_entities_without_rewriting(client):
    response = client.post("/analyze", json={"text": "Mr. Rashi Patil, rashi@acme.co.in"})
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["EMAIL"] == 1
    assert any(e["type"] == "PERSON" for e in body["entities"])


def test_redact_text_returns_clean_text_and_mapping(client):
    response = client.post("/redact/text", json={"text": "Mr. Rashi Patil, rashi@acme.co.in"})
    assert response.status_code == 200
    body = response.json()
    assert "Rashi" not in body["text"]
    assert {row["type"] for row in body["mapping"]} == {"PERSON", "EMAIL"}


def test_redact_text_respects_type_filter(client):
    response = client.post(
        "/redact/text",
        json={"text": "Mr. Rashi Patil, rashi@acme.co.in", "types": ["EMAIL"]},
    )
    body = response.json()
    assert "Rashi Patil" in body["text"]
    assert "rashi@acme.co.in" not in body["text"]


def test_redact_file_returns_a_docx(client):
    payload = b"Mr. Rashi Patil signed on behalf of Acme Wire Industries Limited.\n"
    response = client.post(
        "/redact",
        files={"file": ("ticket.txt", io.BytesIO(payload), "text/plain")},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert int(response.headers["X-PII-Entities-Redacted"]) >= 2
    assert response.content[:2] == b"PK"  # a .docx is a zip
    assert b"Rashi" not in response.content


def test_redact_file_rejects_unsupported_types(client):
    response = client.post(
        "/redact",
        files={"file": ("image.png", io.BytesIO(b"\x89PNG"), "image/png")},
    )
    assert response.status_code == 415


def test_redact_endpoint_does_not_leak_the_mapping(client):
    """The file download must not also hand back the key to reverse it."""
    response = client.post(
        "/redact",
        files={"file": ("t.txt", io.BytesIO(b"Mr. Rashi Patil"), "text/plain")},
    )
    assert "mapping" not in "".join(response.headers.keys()).lower()
