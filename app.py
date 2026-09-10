"""Web service: upload a Word document, get it back with authors renamed."""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from anonymizer import AnonymizeError, anonymize

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_SUFFIXES = {".docx", ".docm", ".dotx", ".dotm"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n]')
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
logger = logging.getLogger(__name__)


def _error(message: str, status: int = 400) -> Response:
    return Response(json.dumps({"error": message}), status=status, mimetype="application/json")


def _content_disposition(original: str) -> str:
    """RFC 6266 disposition with an ASCII fallback.

    HTTP headers are latin-1, so a Cyrillic or accented filename has to travel
    in the percent-encoded ``filename*`` parameter.
    """
    stem = Path(UNSAFE_FILENAME_CHARS.sub("_", original)).stem[:120] or "document"
    filename = f"{stem}_anonymized.docx"
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@app.get("/")
def index() -> str:
    return render_template("index.html", max_mb=MAX_UPLOAD_BYTES // (1024 * 1024))


@app.get("/health")
def health() -> Response:
    return Response('{"status":"ok"}', mimetype="application/json")


@app.post("/anonymize")
def anonymize_endpoint() -> Response:
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error("Choose a Word document first.")
    if Path(upload.filename).suffix.lower() not in ALLOWED_SUFFIXES:
        return _error("Only .docx, .docm, .dotx and .dotm files are supported.")

    replacement = (request.form.get("name") or "").strip()
    if not replacement:
        return _error("Enter the name to write in.")
    if len(replacement) > 100:
        return _error("Keep the replacement name under 100 characters.")
    if CONTROL_CHARS.search(replacement):
        return _error("The replacement name can't contain line breaks or tabs.")

    try:
        result, report = anonymize(
            upload.read(),
            replacement,
            comments_only=request.form.get("scope") == "comments",
            distinct=request.form.get("distinct") == "on",
            strip_metadata=request.form.get("strip_metadata") == "on",
        )
    except AnonymizeError as exc:
        return _error(str(exc))
    except Exception:
        logger.exception("Failed to anonymize upload")
        return _error("Couldn't process that file. It may be corrupt or password-protected.", 500)

    encoded_report = base64.b64encode(json.dumps(report.as_dict()).encode("utf-8")).decode("ascii")
    return Response(
        result,
        mimetype=DOCX_MIME,
        headers={
            "Content-Disposition": _content_disposition(upload.filename),
            "X-Anonymize-Report": encoded_report,
            "Access-Control-Expose-Headers": "X-Anonymize-Report",
        },
    )


@app.errorhandler(RequestEntityTooLarge)
def too_large(_: RequestEntityTooLarge) -> Response:
    return _error(f"That file is over the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.", 413)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
