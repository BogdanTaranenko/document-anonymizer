"""Synthetic .docx fixtures.

A .docx is just a ZIP of XML parts, and ``anonymizer`` works on those bytes
directly, so the tests build documents by hand rather than depending on Word
or python-docx. Every part here is small but shaped like the real thing:
correct namespaces, correct attribute spellings, correct nesting.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'
W16DU = 'xmlns:w16du="http://schemas.microsoft.com/office/word/2023/wordml/word16du"'

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def document(body: str = "", extra_ns: str = "") -> str:
    """A ``word/document.xml`` wrapping the given body XML."""
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {W} {W15} {W16DU} {extra_ns}><w:body>{body}</w:body></w:document>'


def tracked_body(insert_author: str = "Jane Roe", delete_author: str = "John Doe") -> str:
    """A paragraph with an insertion, a deletion and a formatting change."""
    return (
        f'<w:p><w:ins w:id="1" w:author="{insert_author}" w:date="2024-01-01T00:00:00Z">'
        f"<w:r><w:t>inserted text</w:t></w:r></w:ins></w:p>"
        f'<w:p><w:del w:id="2" w:author="{delete_author}" w:date="2024-01-02T00:00:00Z">'
        f"<w:r><w:delText>deleted text</w:delText></w:r></w:del></w:p>"
        f'<w:p><w:pPr><w:pPrChange w:id="3" w:author="{insert_author}" '
        f'w:date="2024-01-03T00:00:00Z"><w:pPr/></w:pPrChange></w:pPr></w:p>'
    )


def comments(*entries: tuple[int, str, str]) -> str:
    """A ``word/comments.xml`` from ``(id, author, initials)`` triples."""
    blocks = "".join(
        f'<w:comment w:id="{cid}" w:author="{author}" w:initials="{initials}" '
        f'w:date="2024-01-01T00:00:00Z"><w:p><w:r><w:t>a note</w:t></w:r></w:p></w:comment>'
        for cid, author, initials in entries
    )
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:comments {W}>{blocks}</w:comments>'


def people(*entries: tuple[str, str]) -> str:
    """A ``word/people.xml`` from ``(author, userId)`` pairs."""
    blocks = "".join(
        f'<w15:person w15:author="{author}">'
        f'<w15:presenceInfo w15:providerId="AD" w15:userId="{user_id}"/>'
        f"</w15:person>"
        for author, user_id in entries
    )
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w15:people {W15} {W}>{blocks}</w15:people>'


def core_properties(creator: str = "Jane Roe", last_modified_by: str = "John Doe") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:creator>{creator}</dc:creator>"
        f"<cp:lastModifiedBy>{last_modified_by}</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )


def build_docx(parts: dict[str, str | bytes] | None = None) -> bytes:
    """Zip the given parts into a .docx, filling in the mandatory ones."""
    contents: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": RELS,
        "word/document.xml": document(),
    }
    contents.update(parts or {})

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in contents.items():
            archive.writestr(name, blob.encode("utf-8") if isinstance(blob, str) else blob)
    return buffer.getvalue()


def part(docx_bytes: bytes, name: str) -> bytes:
    """Read one part out of a .docx."""
    with zipfile.ZipFile(BytesIO(docx_bytes)) as archive:
        return archive.read(name)


def part_names(docx_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(docx_bytes)) as archive:
        return archive.namelist()


@pytest.fixture
def redlined_docx() -> bytes:
    """The workhorse fixture: comments, tracked changes, people, properties."""
    return build_docx(
        {
            "word/document.xml": document(tracked_body()),
            "word/comments.xml": comments((1, "Jane Roe", "JR"), (2, "John Doe", "JD")),
            "word/people.xml": people(
                ("Jane Roe", "jane.roe@corp.example"),
                ("John Doe", "john.doe@corp.example"),
            ),
            "docProps/core.xml": core_properties(),
        }
    )


def all_bytes(docx_bytes: bytes) -> bytes:
    """Every part's *decompressed* content, concatenated.

    Searching the raw archive bytes would be meaningless — the parts are
    deflated, so a name is never there as plain text and ``not in`` passes
    whether or not the rename worked.
    """
    with zipfile.ZipFile(BytesIO(docx_bytes)) as archive:
        return b"\n".join(archive.read(info) for info in archive.infolist() if not info.is_dir())
