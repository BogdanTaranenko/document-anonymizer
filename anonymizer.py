"""Replace every author name in a Word document with a name you choose.

A .docx is a ZIP of XML parts. Author names live in attributes like
``w:author`` (comments and tracked changes), ``w15:author`` (the people
registry), and in ``w15:presenceInfo``, which also carries the author's
account id or work email. This module rewrites those in place, leaving the
rest of the XML byte-identical.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from xml.sax.saxutils import escape, unescape

MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
"""Refuse archives that expand beyond this, to bound zip-bomb damage."""

REWRITABLE_SUFFIXES = (".xml", ".rels")

# Any namespace prefix: w:author, w15:author, w16du:author, oel:author.
AUTHOR_ATTR = re.compile(rb'([A-Za-z][A-Za-z0-9]*:author)="([^"]*)"')
INITIALS_ATTR = re.compile(rb'([A-Za-z][A-Za-z0-9]*:initials)="([^"]*)"')
COMMENT_TAG = re.compile(rb"<w:comment\s[^>]*>")
PRESENCE_INFO = re.compile(rb"<w15:presenceInfo\b[^>]*/>")
PERSON_BLOCK = re.compile(rb"<w15:person\s[^>]*>.*?</w15:person>|<w15:person\s[^>]*/>", re.DOTALL)
CREATOR = re.compile(rb"<dc:creator>[^<]*</dc:creator>")
LAST_MODIFIED_BY = re.compile(rb"<cp:lastModifiedBy>[^<]*</cp:lastModifiedBy>")

COMMENT_PARTS = ("word/comments.xml",)
PEOPLE_PART = "word/people.xml"
CORE_PROPS_PART = "docProps/core.xml"


class AnonymizeError(ValueError):
    """The upload is not a Word document we can process."""


@dataclass
class Report:
    """What the run actually changed — surfaced to the user, not just logged."""

    authors: dict[str, str] = field(default_factory=dict)
    comment_authors_renamed: int = 0
    revision_authors_renamed: int = 0
    initials_renamed: int = 0
    presence_info_removed: int = 0
    metadata_cleared: bool = False
    leftover_names: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "authors": [
                {"original": original, "replacement": replacement}
                for original, replacement in self.authors.items()
            ],
            "comment_authors_renamed": self.comment_authors_renamed,
            "revision_authors_renamed": self.revision_authors_renamed,
            "initials_renamed": self.initials_renamed,
            "presence_info_removed": self.presence_info_removed,
            "metadata_cleared": self.metadata_cleared,
            "leftover_names": self.leftover_names,
        }


def _attr_escape(value: str) -> bytes:
    return escape(value, {'"': "&quot;"}).encode("utf-8")


def _initials_for(name: str) -> str:
    """First letter of each of the first two words, plus any trailing digit.

    "Anonymous" -> "A"; "Anonymous 2" -> "A2"; "Jane Roe" -> "JR".
    """
    words = name.split()
    if not words:
        return "A"
    if len(words) >= 2 and words[-1].isdigit():
        return (words[0][0] + words[-1]).upper()
    return "".join(word[0] for word in words[:2]).upper()


def _iter_author_values(data: bytes, comments_only: bool) -> list[bytes]:
    """Author attribute values in first-appearance order.

    With ``comments_only`` we look only at ``<w:comment>`` element tags, so a
    tracked change made *inside* a comment's text is left alone.
    """
    haystacks = COMMENT_TAG.findall(data) if comments_only else [data]
    return [match.group(2) for chunk in haystacks for match in AUTHOR_ATTR.finditer(chunk)]


def _rewrite_authors(data: bytes, mapping: dict[bytes, bytes]) -> tuple[bytes, int]:
    count = 0

    def substitute(match: re.Match[bytes]) -> bytes:
        nonlocal count
        replacement = mapping.get(match.group(2))
        if replacement is None:
            return match.group(0)
        count += 1
        return b'%s="%s"' % (match.group(1), replacement)

    return AUTHOR_ATTR.sub(substitute, data), count


def _rewrite_comment_tags(data: bytes, mapping: dict[bytes, bytes]) -> tuple[bytes, int]:
    """Rewrite authors only on ``<w:comment>`` element tags."""
    count = 0

    def substitute(match: re.Match[bytes]) -> bytes:
        nonlocal count
        rewritten, hits = _rewrite_authors(match.group(0), mapping)
        count += hits
        return rewritten

    return COMMENT_TAG.sub(substitute, data), count


def _rewrite_initials(data: bytes, initials: bytes, comments_only: bool) -> tuple[bytes, int]:
    count = 0

    def substitute(match: re.Match[bytes]) -> bytes:
        nonlocal count
        count += 1
        return b'%s="%s"' % (match.group(1), initials)

    if comments_only:

        def per_comment(match: re.Match[bytes]) -> bytes:
            return INITIALS_ATTR.sub(substitute, match.group(0))

        return COMMENT_TAG.sub(per_comment, data), count
    return INITIALS_ATTR.sub(substitute, data), count


def _rewrite_people(data: bytes, mapping: dict[bytes, bytes]) -> tuple[bytes, int, int]:
    """Rename person entries, dropping presence info only for renamed authors.

    ``w15:presenceInfo`` holds the author's directory id or work email, so it
    has to go with the name — but an author the caller chose to keep should
    keep their record intact.
    """
    renamed = 0
    removed = 0

    def substitute(match: re.Match[bytes]) -> bytes:
        nonlocal renamed, removed
        block = match.group(0)
        author = AUTHOR_ATTR.search(block)
        if author is None or author.group(2) not in mapping:
            return block
        block, hits = _rewrite_authors(block, mapping)
        renamed += hits
        block, drops = PRESENCE_INFO.subn(b"", block)
        removed += drops
        return block

    return PERSON_BLOCK.sub(substitute, data), renamed, removed


def _read_parts(archive: zipfile.ZipFile) -> dict[str, bytes]:
    total = sum(info.file_size for info in archive.infolist())
    if total > MAX_UNCOMPRESSED_BYTES:
        raise AnonymizeError("This archive expands to more than 400 MB. Refusing to open it.")
    parts: dict[str, bytes] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        parts[info.filename] = archive.read(info)
    return parts


def _find_leftovers(parts: dict[str, bytes], originals: list[str]) -> list[str]:
    """Names still in the file *outside* author fields — body text, metadata, OLE.

    Author attributes are stripped from a scratch copy first, so an author the
    caller deliberately kept is not reported. Embedded OLE objects store text
    as UTF-16, so a UTF-8-only scan would miss a name that is still in there.
    """
    haystacks = []
    for name, blob in parts.items():
        if name.endswith(REWRITABLE_SUFFIXES):
            blob = AUTHOR_ATTR.sub(b"", blob)
            blob = INITIALS_ATTR.sub(b"", blob)
        haystacks.append(blob.lower())

    leftovers: list[str] = []
    for name in originals:
        needles = [name.lower().encode("utf-8"), name.lower().encode("utf-16-le")]
        if any(needle in blob for blob in haystacks for needle in needles):
            leftovers.append(name)
    return leftovers


def anonymize(
    docx_bytes: bytes,
    replacement: str,
    *,
    comments_only: bool = False,
    distinct: bool = False,
    strip_metadata: bool = True,
) -> tuple[bytes, Report]:
    """Return a copy of ``docx_bytes`` with every author renamed.

    Args:
        replacement: the name to write in.
        comments_only: rename comment authors but leave tracked changes alone.
        distinct: give each original author a numbered name ("Anonymous 1",
            "Anonymous 2") so you can still tell the parties apart.
        strip_metadata: also clear the document's creator and last-modified-by.
    """
    replacement = replacement.strip()
    if not replacement:
        raise AnonymizeError("Give me a replacement name.")

    try:
        source = zipfile.ZipFile(BytesIO(docx_bytes))
    except zipfile.BadZipFile as exc:
        raise AnonymizeError("That file isn't a Word document (it isn't a ZIP archive).") from exc

    with source:
        if "word/document.xml" not in source.namelist():
            raise AnonymizeError("That file is a ZIP but has no word/document.xml inside.")
        parts = _read_parts(source)
        order = [info.filename for info in source.infolist() if not info.is_dir()]
        entries = {
            info.filename: (info.compress_type, info.date_time)
            for info in source.infolist()
            if not info.is_dir()
        }

    report = Report()

    # Pass 1 — collect author names in first-appearance order.
    scan_order = COMMENT_PARTS + tuple(n for n in order if n not in COMMENT_PARTS)
    seen: list[bytes] = []
    for name in scan_order:
        blob = parts.get(name)
        if blob is None or not name.endswith(REWRITABLE_SUFFIXES):
            continue
        if comments_only and name not in COMMENT_PARTS:
            continue
        for value in _iter_author_values(
            blob, comments_only=comments_only and name in COMMENT_PARTS
        ):
            if value not in seen:
                seen.append(value)

    if not seen:
        raise AnonymizeError("No comment or tracked-change authors found in this document.")

    mapping: dict[bytes, bytes] = {}
    for index, value in enumerate(seen, start=1):
        name = f"{replacement} {index}" if distinct and len(seen) > 1 else replacement
        mapping[value] = _attr_escape(name)
        report.authors[unescape(value.decode("utf-8"))] = name

    # A single initials value can't represent several distinct names, so in
    # distinct mode we key initials off each author's own new name instead.
    default_initials = _attr_escape(_initials_for(replacement))

    # Pass 2 — rewrite.
    for name in order:
        blob = parts[name]
        if not name.endswith(REWRITABLE_SUFFIXES):
            continue

        if name in COMMENT_PARTS:
            if comments_only:
                blob, hits = _rewrite_comment_tags(blob, mapping)
            else:
                blob, hits = _rewrite_authors(blob, mapping)
            report.comment_authors_renamed += hits
            blob, initials_hits = _rewrite_initials(
                blob, default_initials, comments_only=comments_only
            )
            report.initials_renamed += initials_hits
        elif name == PEOPLE_PART:
            blob, _, removed = _rewrite_people(blob, mapping)
            report.presence_info_removed += removed
        elif not comments_only:
            blob, hits = _rewrite_authors(blob, mapping)
            report.revision_authors_renamed += hits

        if strip_metadata and name == CORE_PROPS_PART:
            blob, creators = CREATOR.subn(b"<dc:creator></dc:creator>", blob)
            blob, modifiers = LAST_MODIFIED_BY.subn(
                b"<cp:lastModifiedBy></cp:lastModifiedBy>", blob
            )
            report.metadata_cleared = bool(creators or modifiers)

        parts[name] = blob

    report.leftover_names = _find_leftovers(parts, list(report.authors))

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for name in order:
            compress_type, date_time = entries[name]
            target.writestr(
                zipfile.ZipInfo(name, date_time=date_time),
                parts[name],
                compress_type=compress_type,
            )
    return buffer.getvalue(), report
