"""Integration tests for the HTTP layer."""

from __future__ import annotations

import base64
import json
from io import BytesIO

import pytest
from conftest import build_docx, comments, document, tracked_body

from app import MAX_UPLOAD_BYTES, _content_disposition, app


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def upload(redlined_docx):
    def make(filename: str = "contract.docx", data: bytes | None = None):
        return (BytesIO(redlined_docx if data is None else data), filename)

    return make


# --------------------------------------------------------------------------
# Static routes
# --------------------------------------------------------------------------


def test_index_renders_the_upload_form(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"<form" in response.data
    assert b'name="file"' in response.data
    assert str(MAX_UPLOAD_BYTES // (1024 * 1024)).encode() in response.data


def test_health_reports_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


def test_rejects_a_request_with_no_file(client):
    response = client.post("/anonymize", data={"name": "Anonymous"})

    assert response.status_code == 400
    assert "Choose a Word document" in response.get_json()["error"]


def test_rejects_an_empty_filename(client):
    response = client.post("/anonymize", data={"name": "Anonymous", "file": (BytesIO(b"x"), "")})

    assert response.status_code == 400


@pytest.mark.parametrize("filename", ["notes.txt", "sheet.xlsx", "scan.pdf", "archive.zip"])
def test_rejects_an_unsupported_extension(client, upload, filename):
    response = client.post("/anonymize", data={"name": "Anonymous", "file": upload(filename)})

    assert response.status_code == 400
    assert "Only .docx" in response.get_json()["error"]


@pytest.mark.parametrize("filename", ["a.docx", "a.docm", "a.dotx", "a.dotm", "A.DOCX"])
def test_accepts_every_supported_extension(client, upload, filename):
    response = client.post("/anonymize", data={"name": "Anonymous", "file": upload(filename)})

    assert response.status_code == 200


@pytest.mark.parametrize("name", ["", "   "])
def test_rejects_a_blank_replacement_name(client, upload, name):
    response = client.post("/anonymize", data={"name": name, "file": upload()})

    assert response.status_code == 400
    assert "Enter the name" in response.get_json()["error"]


def test_rejects_a_replacement_name_over_100_characters(client, upload):
    response = client.post("/anonymize", data={"name": "x" * 101, "file": upload()})

    assert response.status_code == 400
    assert "under 100 characters" in response.get_json()["error"]


@pytest.mark.parametrize("name", ["Jane\nRoe", "Jane\tRoe", "Jane\x00Roe", "Jane\rRoe"])
def test_rejects_control_characters_in_the_replacement_name(client, upload, name):
    response = client.post("/anonymize", data={"name": name, "file": upload()})

    assert response.status_code == 400
    assert "line breaks or tabs" in response.get_json()["error"]


def test_rejects_a_file_that_is_not_a_word_document(client, upload):
    response = client.post(
        "/anonymize",
        data={"name": "Anonymous", "file": upload("fake.docx", b"definitely not a zip")},
    )

    assert response.status_code == 400
    assert "ZIP archive" in response.get_json()["error"]


def test_rejects_a_document_with_no_authors(client, upload):
    plain = build_docx({"word/document.xml": document("<w:p><w:r><w:t>hi</w:t></w:r></w:p>")})
    response = client.post(
        "/anonymize", data={"name": "Anonymous", "file": upload("plain.docx", plain)}
    )

    assert response.status_code == 400
    assert "No comment or tracked-change authors" in response.get_json()["error"]


def test_rejects_an_upload_over_the_size_limit(client, upload):
    app.config["MAX_CONTENT_LENGTH"] = 512
    try:
        response = client.post(
            "/anonymize",
            data={"name": "Anonymous", "file": upload("big.docx", b"x" * 4096)},
        )
    finally:
        app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    assert response.status_code == 413
    assert "limit" in response.get_json()["error"]


def test_an_unexpected_failure_is_reported_without_leaking_internals(client, upload, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("app.anonymize", explode)
    response = client.post("/anonymize", data={"name": "Anonymous", "file": upload()})

    assert response.status_code == 500
    body = response.get_json()["error"]
    assert "secret internal detail" not in body
    assert "corrupt or password-protected" in body


# --------------------------------------------------------------------------
# Successful responses
# --------------------------------------------------------------------------


def test_returns_a_docx_with_a_decodable_report(client, upload):
    response = client.post("/anonymize", data={"name": "Anonymous", "file": upload()})

    assert response.status_code == 200
    assert response.mimetype == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.data.startswith(b"PK")

    report = json.loads(base64.b64decode(response.headers["X-Anonymize-Report"]))
    assert report["authors"] == [
        {"original": "Jane Roe", "replacement": "Anonymous"},
        {"original": "John Doe", "replacement": "Anonymous"},
    ]
    assert report["comment_authors_renamed"] == 2
    assert response.headers["Access-Control-Expose-Headers"] == "X-Anonymize-Report"


def test_the_report_header_is_latin1_safe_for_a_non_ascii_author_name(client, upload):
    docx = build_docx({"word/comments.xml": comments((1, "Ірина Шевченко", "ІШ"))})
    response = client.post("/anonymize", data={"name": "Anonymous", "file": upload("a.docx", docx)})

    assert response.status_code == 200
    report = json.loads(base64.b64decode(response.headers["X-Anonymize-Report"]))
    assert report["authors"] == [{"original": "Ірина Шевченко", "replacement": "Anonymous"}]


def test_the_scope_option_is_passed_through(client, upload):
    response = client.post(
        "/anonymize", data={"name": "Anonymous", "scope": "comments", "file": upload()}
    )

    report = json.loads(base64.b64decode(response.headers["X-Anonymize-Report"]))
    assert report["revision_authors_renamed"] == 0
    assert report["comment_authors_renamed"] == 2


def test_the_distinct_option_is_passed_through(client, upload):
    response = client.post("/anonymize", data={"name": "Party", "distinct": "on", "file": upload()})

    report = json.loads(base64.b64decode(response.headers["X-Anonymize-Report"]))
    assert [a["replacement"] for a in report["authors"]] == ["Party 1", "Party 2"]


def test_metadata_is_left_alone_unless_the_checkbox_is_ticked(client, upload):
    without = client.post("/anonymize", data={"name": "Anonymous", "file": upload()})
    with_flag = client.post(
        "/anonymize", data={"name": "Anonymous", "strip_metadata": "on", "file": upload()}
    )

    assert (
        json.loads(base64.b64decode(without.headers["X-Anonymize-Report"]))["metadata_cleared"]
        is False
    )
    assert (
        json.loads(base64.b64decode(with_flag.headers["X-Anonymize-Report"]))["metadata_cleared"]
        is True
    )


def test_leftover_names_reach_the_client(client, upload):
    body = tracked_body() + "<w:p><w:r><w:t>as signed by Jane Roe</w:t></w:r></w:p>"
    docx = build_docx({"word/document.xml": document(body)})
    response = client.post("/anonymize", data={"name": "Anonymous", "file": upload("a.docx", docx)})

    report = json.loads(base64.b64decode(response.headers["X-Anonymize-Report"]))
    assert report["leftover_names"] == ["Jane Roe"]


# --------------------------------------------------------------------------
# Content-Disposition
# --------------------------------------------------------------------------


def test_disposition_carries_both_an_ascii_and_a_utf8_filename():
    header = _content_disposition("Договір.docx")

    assert 'filename="' in header
    assert "filename*=UTF-8''" in header
    header.encode("latin-1")  # HTTP headers must survive latin-1


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("contract.docx", "contract_anonymized.docx"),
        # Path separators are flattened before the stem is taken, so a crafted
        # filename can never climb out of the download name.
        ("mid/way.docx", "mid_way_anonymized.docx"),
        ("../../etc/passwd.docx", ".._.._etc_passwd_anonymized.docx"),
        ('bad:name*?".docx', "bad_name____anonymized.docx"),
        (".docx", ".docx_anonymized.docx"),
        (".", "document_anonymized.docx"),
    ],
)
def test_disposition_sanitises_the_original_filename(original, expected):
    assert f'filename="{expected}"' in _content_disposition(original)


def test_disposition_truncates_a_very_long_filename():
    header = _content_disposition("x" * 500 + ".docx")

    assert 'filename="' + "x" * 120 + '_anonymized.docx"' in header


def test_the_response_offers_the_file_as_a_download(client, upload):
    response = client.post(
        "/anonymize", data={"name": "Anonymous", "file": upload("Some Contract.docx")}
    )

    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert "Some Contract_anonymized.docx" in disposition


def test_a_hostile_author_name_cannot_break_out_of_the_response_headers(client, upload):
    """The report travels base64-encoded, so document content can't inject headers.

    The page escapes these names again before rendering (see ``esc`` in
    templates/index.html) — the report itself must stay faithful.
    """
    hostile = "<img src=x onerror=alert(1)>"
    docx = build_docx({"word/comments.xml": comments((1, hostile.replace("<", "&lt;"), "X"))})

    response = client.post("/anonymize", data={"name": "Anonymous", "file": upload("a.docx", docx)})

    assert response.status_code == 200
    raw_header = response.headers["X-Anonymize-Report"]
    assert "\r" not in raw_header and "\n" not in raw_header
    assert "<" not in raw_header and ">" not in raw_header

    report = json.loads(base64.b64decode(raw_header))
    assert report["authors"] == [{"original": hostile, "replacement": "Anonymous"}]
