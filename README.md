# Redline Anonymizer

[![CI](https://github.com/BogdanTaranenko/document-anonymizer/actions/workflows/ci.yml/badge.svg)](https://github.com/BogdanTaranenko/document-anonymizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

Upload a Word document, get it back with every author name replaced by one you
choose. Comments, tracked changes, the hidden author registry and the document
properties — all of it, in one pass, without touching a word of the text.

**[Try it](https://document-anonymizer-production.up.railway.app)** — a demo
instance, no sign-up. Read [the limits](#limits) before you trust it with
anything real.

## Why

Send a marked-up contract to the other side and you send more than your edits.
Word stores the author of every comment and every tracked change, and
`word/people.xml` quietly keeps a directory id or work email for each of them.
"Accept all changes" does not remove any of it. Neither does copying the text
into a new document, if you keep the redlines.

This renames all of it at once and then tells you what it could not reach.

## What it renames

| Where | Attribute |
|---|---|
| Comments | `w:author`, `w:initials` on `<w:comment>` |
| Tracked insertions, deletions, formatting changes | `w:author` on `w:ins`, `w:del`, `w:*Change` |
| Author registry (`word/people.xml`) | `w15:author`, plus the `w15:presenceInfo` record that stores the author's directory id or work email |
| Document properties (optional) | `dc:creator`, `cp:lastModifiedBy` |

Headers, footers, footnotes and endnotes are covered too — every XML part in the
package is rewritten. The document's text is never touched.

Author attributes are matched under any namespace prefix, so parts Word invents
in later versions (`w16du:author` and friends) are caught without a code change.

## Options

- **Comments only** — rename comment authors and leave redline authors intact.
  A tracked change made *inside* a comment is left alone too.
- **Number each author separately** — writes `Anonymous 1`, `Anonymous 2`, so you
  can still tell which party proposed which edit. Without it, everyone collapses
  into one identity.
- **Clear document properties** — empties creator and last-modified-by.

After each run the service scans every part of the output, including embedded
OLE objects in UTF-16, and reports any original name still present outside an
author field.

## Run it

```bash
git clone https://github.com/BogdanTaranenko/document-anonymizer.git
cd document-anonymizer

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python app.py              # http://127.0.0.1:5000
```

Python 3.12 or newer.

For anything beyond local use, put it behind a real WSGI server:

```bash
gunicorn -w 2 -b 127.0.0.1:5000 app:app
```

Two workers, not four: `_read_parts` holds every ZIP part in memory and
`_find_leftovers` builds a second copy, so peak memory per in-flight request
can exceed 1 GB against the 400 MB uncompressed ceiling.

## Use it as a library

`anonymizer.py` has no Flask dependency and does no I/O. Copy it into your
project and call it:

```python
from anonymizer import anonymize

result, report = anonymize(
    open("contract.docx", "rb").read(),
    "Anonymous",
    distinct=True,
)
open("out.docx", "wb").write(result)
print(report.as_dict())
```

`anonymize` raises `AnonymizeError` — a `ValueError` subclass with a
user-presentable message — for anything it cannot process.

## Tests

```bash
pip install -r requirements-dev.txt
pytest --cov --cov-report=term-missing
```

88 tests, 100% coverage of both modules. Every fixture is a document built from
scratch in `tests/conftest.py`; no `.docx` file is committed to this repository,
by policy.

## Deployment

The demo runs on [Railway](https://railway.app) — one service, no database.

```bash
railway up          # deploy the working directory
railway logs        # tail the running service
railway status      # what's linked
```

The start command lives in the **`Procfile`**, which Railpack honours directly.
Do not move it into `railway.json`: the `startCommand` key there is silently
ignored, the build still reports success, and the service then serves 502s
because Railpack falls back to guessing `gunicorn main:app`.

`railway.json` carries only the healthcheck and restart policy.
Railway is retiring config-as-code on **2026-12-01**; after that those settings
have to be set on the service itself (dashboard, or `railway api` with the
`serviceInstanceUpdate` mutation). The `Procfile` is unaffected.

`.python-version` pins the interpreter; `.railwayignore` keeps `.venv/` and
`.idea/` out of the upload.

Any host that reads a `Procfile` or lets you set a start command will work just
as well — there is nothing Railway-specific in the application.

## Limits

- 25 MB upload, 400 MB uncompressed (zip-bomb guard). Both are constants at the
  top of the modules.
- Files are processed in memory and never written to disk.
- Password-protected documents can't be opened and are rejected.
- **Renaming is not redaction.** If a name appears in the body text of the
  contract, it stays. The report tells you when that happens. Identity can also
  leak through revision timestamps and RSIDs, which are left intact.

[SECURITY.md](SECURITY.md) spells out what the tool does and does not protect
against. Read it before relying on this for anything that matters, and always
open the result and check it yourself.

## Contributing

Bug reports and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Please never attach a real document to an issue.

## Licence

[MIT](LICENSE)
