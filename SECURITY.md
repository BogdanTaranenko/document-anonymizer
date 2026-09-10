# Security

## Reporting a vulnerability

Report privately through
[GitHub Security Advisories](https://github.com/BogdanTaranenko/document-anonymizer/security/advisories/new).
Please don't open a public issue for anything exploitable.

Include what you did, what happened, and — if a document is involved — a
**synthetic** file that reproduces it. Never send a real client document.

Expect a first response within a week. This is a small project maintained in
spare time; there is no paid support and no formal SLA.

## What this tool does and does not do

Read this before relying on it for anything that matters.

**It renames. It does not redact.** The tool rewrites author attributes —
`w:author`, `w:initials`, `w15:author`, the `w15:presenceInfo` record, and
optionally `dc:creator` / `cp:lastModifiedBy`. The document's *text* is never
touched. If a name appears in the body of the contract, in a header, in a
footnote, or inside an embedded object, **it stays there**.

After every run the tool scans the output for the original names and reports any
that survived, including UTF-16 text inside embedded OLE objects. Read that
report. An empty `leftover_names` list means the tool found nothing — it is not
a guarantee that nothing is left.

**Known limits:**

- Identity can leak through things other than names: revision timestamps
  (`w:date`), revision ids, RSID values, custom document properties, and
  thumbnails are all left intact.
- With `distinct` numbering on, the mapping preserves who-said-what. That is the
  point of the option, but it means the parties remain distinguishable.
- The leftover scan is case-insensitive for ASCII only. A non-ASCII name that
  appears in the body in a different case may not be reported.
- Password-protected and otherwise encrypted documents are rejected, not
  processed.

**Always open the result and check it yourself before sending it to anyone.**

## Running your own instance

- Documents are processed entirely in memory and never written to disk. Nothing
  is logged except exception tracebacks, which do not contain document content.
- There is no authentication. If you deploy this, anyone who can reach the URL
  can upload to it. Put it behind auth or a private network if that matters.
- Uploads are capped at 25 MB, and archives that expand past 400 MB are refused
  as zip bombs. Both are constants at the top of `app.py` and `anonymizer.py`.
- `_read_parts` holds every part in memory and the leftover scan builds a second
  copy, so peak memory per in-flight request can exceed 1 GB at the 400 MB
  ceiling. Size your workers accordingly — two, not four, on a small instance.
- The public instance linked from the README is a convenience demo run by the
  maintainer on a personal account. It carries no uptime or confidentiality
  guarantee. For anything sensitive, run it locally.

## Supported versions

Fixes land on `main`. There are no released versions and no backports.
