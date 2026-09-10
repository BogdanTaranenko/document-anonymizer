# Contributing

Thanks for looking. This is a small, deliberately boring codebase — two Python
modules and one HTML template. Keeping it that way is a feature.

## Setting up

```bash
git clone https://github.com/BogdanTaranenko/document-anonymizer.git
cd document-anonymizer

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest
python app.py    # http://127.0.0.1:5000
```

Python 3.12 or newer. CI runs 3.12 and 3.13.

## Before you open a PR

```bash
ruff check .
ruff format --check .
pytest --cov --cov-report=term-missing
```

CI runs exactly these and fails under 95% coverage. Both modules are at 100%
today; please keep them there.

## Never commit a real document

No `.docx` files in the repository, ever — not as a fixture, not as an issue
attachment, not in a PR description. Real documents carry exactly the names this
tool exists to remove.

Tests build documents from scratch instead. `tests/conftest.py` has helpers for
the parts that matter (`document`, `tracked_body`, `comments`, `people`,
`core_properties`, `build_docx`). If you need a shape they don't cover, add it
there rather than reaching for a file.

One trap worth knowing: the output archive is deflated, so
`assert b"Jane Roe" not in result` passes whether or not the rename worked. Use
the `all_bytes` helper, which searches decompressed parts.

## How the code is organised

- `anonymizer.py` — all the rewriting. Pure function, no I/O, no Flask. This is
  where document logic belongs.
- `app.py` — HTTP only: validate the request, call `anonymize`, shape the
  response. No document knowledge.
- `templates/index.html` — the whole frontend, inlined. No build step, no
  framework, vanilla ES modules.

The project is intentionally **not** a packaged distribution. It is a flat
two-module service so it can be read top to bottom and deployed by copying the
directory. Please don't restructure it into `src/` layout.

## Things to know about the rewriting

It operates on raw XML bytes with regular expressions rather than a parser, and
that is deliberate: every part outside the author attributes stays
byte-identical, so Word never sees a reserialised file it might reject. If you
change this area:

- Keep author matching namespace-agnostic. Word invents new prefixes
  (`w16du:author` and friends) and the pattern is written to catch them.
- Escape anything you write into an attribute (`_attr_escape`), and unescape
  anything you put into the report.
- Preserve part order and per-part compression when rebuilding the archive.
- Add a test that proves the output still parses as XML.

Values that come out of an uploaded document — author names above all — are
attacker-controlled. On the way into the page they go through `esc()` in
`templates/index.html`; on the way into XML they go through `_attr_escape`.
Don't add a path that skips either.

## Commits and PRs

Conventional commits (`fix:`, `feat:`, `docs:`, `test:`, `refactor:`) are
preferred but not enforced. One logical change per PR, and say in the
description what behaviour is different afterwards.

For anything larger than a bug fix, open an issue first — it's cheaper than
finding out the change isn't wanted after you've written it.
