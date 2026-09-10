"""Behavioural tests for the rewriting core."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

import pytest
from conftest import (
    all_bytes,
    build_docx,
    comments,
    core_properties,
    document,
    part,
    part_names,
    people,
    tracked_body,
)

import anonymizer
from anonymizer import AnonymizeError, _initials_for, anonymize

# --------------------------------------------------------------------------
# Rejections
# --------------------------------------------------------------------------


def test_rejects_a_file_that_is_not_a_zip():
    with pytest.raises(AnonymizeError, match="isn't a ZIP archive"):
        anonymize(b"this is a plain text file", "Anonymous")


def test_rejects_a_zip_without_a_word_document():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a word document")

    with pytest.raises(AnonymizeError, match=re.escape("no word/document.xml")):
        anonymize(buffer.getvalue(), "Anonymous")


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_rejects_a_blank_replacement_name(redlined_docx, blank):
    with pytest.raises(AnonymizeError, match="replacement name"):
        anonymize(redlined_docx, blank)


def test_rejects_a_document_with_no_authors():
    plain = build_docx({"word/document.xml": document("<w:p><w:r><w:t>hello</w:t></w:r></w:p>")})
    with pytest.raises(AnonymizeError, match="No comment or tracked-change authors"):
        anonymize(plain, "Anonymous")


def test_refuses_an_archive_that_expands_past_the_ceiling(redlined_docx, monkeypatch):
    monkeypatch.setattr(anonymizer, "MAX_UNCOMPRESSED_BYTES", 16)
    with pytest.raises(AnonymizeError, match="Refusing to open it"):
        anonymize(redlined_docx, "Anonymous")


# --------------------------------------------------------------------------
# Renaming
# --------------------------------------------------------------------------


def test_renames_every_comment_and_revision_author(redlined_docx):
    result, report = anonymize(redlined_docx, "Anonymous")

    plain = all_bytes(result)
    assert b"Jane Roe" not in plain
    assert b"John Doe" not in plain
    assert report.authors == {"Jane Roe": "Anonymous", "John Doe": "Anonymous"}
    assert report.comment_authors_renamed == 2
    assert report.revision_authors_renamed == 3


def test_renames_authors_in_headers_footers_and_footnotes():
    docx = build_docx(
        {
            "word/document.xml": document(tracked_body()),
            "word/header1.xml": document(tracked_body("Header Author", "Header Author")),
            "word/footnotes.xml": document(tracked_body("Footnote Author", "Footnote Author")),
            "word/endnotes.xml": document(tracked_body("Endnote Author", "Endnote Author")),
        }
    )
    result, report = anonymize(docx, "Anonymous")

    plain = all_bytes(result)
    for name in (b"Header Author", b"Footnote Author", b"Endnote Author"):
        assert name not in plain
    assert set(report.authors) == {
        "Jane Roe",
        "John Doe",
        "Header Author",
        "Footnote Author",
        "Endnote Author",
    }


def test_renames_authors_under_any_namespace_prefix():
    body = '<w:p><w16du:dateUtc w16du:author="Future Person" w16du:id="4"/></w:p>'
    docx = build_docx({"word/document.xml": document(tracked_body() + body)})

    result, report = anonymize(docx, "Anonymous")

    assert b"Future Person" not in all_bytes(result)
    assert "Future Person" in report.authors


def test_rewrites_comment_initials(redlined_docx):
    result, report = anonymize(redlined_docx, "Anonymous")

    plain = all_bytes(result)
    assert b'w:initials="JR"' not in plain
    assert b'w:initials="JD"' not in plain
    assert plain.count(b'w:initials="A"') == 2
    assert report.initials_renamed == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Anonymous", "A"),
        ("Anonymous 2", "A2"),
        ("Jane Roe", "JR"),
        ("Jane Amelia Roe", "JA"),
        ("Counterparty 12", "C12"),
        ("", "A"),
        ("   ", "A"),
    ],
)
def test_initials_are_derived_from_the_replacement_name(name, expected):
    assert _initials_for(name) == expected


# --------------------------------------------------------------------------
# Distinct numbering
# --------------------------------------------------------------------------


def test_distinct_numbers_each_author_separately(redlined_docx):
    _, report = anonymize(redlined_docx, "Anonymous", distinct=True)

    assert report.authors == {"Jane Roe": "Anonymous 1", "John Doe": "Anonymous 2"}


def test_distinct_numbers_in_first_appearance_order_with_comments_first():
    docx = build_docx(
        {
            "word/document.xml": document(tracked_body("Third Seen", "Fourth Seen")),
            "word/comments.xml": comments((1, "First Seen", "FS"), (2, "Second Seen", "SS")),
        }
    )
    _, report = anonymize(docx, "Party", distinct=True)

    assert list(report.authors.values()) == ["Party 1", "Party 2", "Party 3", "Party 4"]
    assert list(report.authors) == ["First Seen", "Second Seen", "Third Seen", "Fourth Seen"]


def test_distinct_does_not_number_a_lone_author():
    docx = build_docx({"word/document.xml": document(tracked_body("Solo", "Solo"))})

    _, report = anonymize(docx, "Anonymous", distinct=True)

    assert report.authors == {"Solo": "Anonymous"}


def test_without_distinct_every_author_collapses_into_one(redlined_docx):
    _, report = anonymize(redlined_docx, "Anonymous")

    assert set(report.authors.values()) == {"Anonymous"}


# --------------------------------------------------------------------------
# comments_only
# --------------------------------------------------------------------------


def test_comments_only_leaves_revision_authors_alone(redlined_docx):
    result, report = anonymize(redlined_docx, "Anonymous", comments_only=True)

    assert b'w:author="Jane Roe"' in part(result, "word/document.xml")
    assert report.revision_authors_renamed == 0
    assert report.comment_authors_renamed == 2
    assert part(result, "word/comments.xml").count(b'w:author="Anonymous"') == 2


def test_comments_only_leaves_a_tracked_change_inside_a_comment_alone():
    nested = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:comment w:id="1" w:author="Comment Author" w:initials="CA" w:date="2024-01-01T00:00:00Z">'
        '<w:p><w:ins w:id="9" w:author="Nested Author" w:date="2024-01-01T00:00:00Z">'
        "<w:r><w:t>edit inside the comment</w:t></w:r></w:ins></w:p>"
        "</w:comment></w:comments>"
    )
    docx = build_docx({"word/document.xml": document(tracked_body()), "word/comments.xml": nested})

    result, report = anonymize(docx, "Anonymous", comments_only=True)

    assert report.authors == {"Comment Author": "Anonymous"}
    assert b'w:author="Nested Author"' in part(result, "word/comments.xml")


def test_all_scope_does_rename_a_tracked_change_inside_a_comment():
    nested = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:comment w:id="1" w:author="Comment Author" w:initials="CA" w:date="2024-01-01T00:00:00Z">'
        '<w:p><w:ins w:id="9" w:author="Nested Author" w:date="2024-01-01T00:00:00Z">'
        "<w:r><w:t>edit inside the comment</w:t></w:r></w:ins></w:p>"
        "</w:comment></w:comments>"
    )
    docx = build_docx({"word/comments.xml": nested})

    result, report = anonymize(docx, "Anonymous")

    assert b"Nested Author" not in all_bytes(result)
    assert report.comment_authors_renamed == 2


# --------------------------------------------------------------------------
# people.xml
# --------------------------------------------------------------------------


def test_removes_the_presence_record_of_a_renamed_author(redlined_docx):
    result, report = anonymize(redlined_docx, "Anonymous")
    registry = part(result, "word/people.xml")

    assert b"jane.roe@corp.example" not in registry
    assert b"john.doe@corp.example" not in registry
    assert b"presenceInfo" not in registry
    assert report.presence_info_removed == 2


def test_keeps_the_presence_record_of_an_author_the_caller_kept():
    docx = build_docx(
        {
            "word/document.xml": document(tracked_body("Redline Only", "Redline Only")),
            "word/comments.xml": comments((1, "Commenter", "C")),
            "word/people.xml": people(
                ("Commenter", "commenter@corp.example"),
                ("Redline Only", "redline@corp.example"),
            ),
        }
    )

    result, report = anonymize(docx, "Anonymous", comments_only=True)
    registry = part(result, "word/people.xml")

    assert b"commenter@corp.example" not in registry
    assert b"redline@corp.example" in registry
    assert b'w15:author="Redline Only"' in registry
    assert report.presence_info_removed == 1


def test_handles_a_self_closing_person_element():
    registry = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
        '<w15:person w15:author="Jane Roe"/>'
        "</w15:people>"
    )
    docx = build_docx({"word/document.xml": document(tracked_body()), "word/people.xml": registry})

    result, _ = anonymize(docx, "Anonymous")

    assert b"Jane Roe" not in all_bytes(result)


# --------------------------------------------------------------------------
# Document properties
# --------------------------------------------------------------------------


def test_clears_document_properties_by_default(redlined_docx):
    result, report = anonymize(redlined_docx, "Anonymous")
    props = part(result, "docProps/core.xml")

    assert b"<dc:creator></dc:creator>" in props
    assert b"<cp:lastModifiedBy></cp:lastModifiedBy>" in props
    assert report.metadata_cleared is True


def test_leaves_document_properties_when_asked(redlined_docx):
    result, report = anonymize(redlined_docx, "Anonymous", strip_metadata=False)
    props = part(result, "docProps/core.xml")

    assert b"<dc:creator>Jane Roe</dc:creator>" in props
    assert report.metadata_cleared is False


def test_metadata_cleared_is_false_when_there_are_no_properties_to_clear():
    docx = build_docx({"word/document.xml": document(tracked_body())})

    _, report = anonymize(docx, "Anonymous")

    assert report.metadata_cleared is False


# --------------------------------------------------------------------------
# Leftover detection
# --------------------------------------------------------------------------


def test_reports_a_name_that_survives_in_the_body_text():
    body = (
        tracked_body()
        + "<w:p><w:r><w:t>Signed by Jane Roe on behalf of the seller.</w:t></w:r></w:p>"
    )
    docx = build_docx({"word/document.xml": document(body)})

    _, report = anonymize(docx, "Anonymous")

    assert report.leftover_names == ["Jane Roe"]


def test_reports_nothing_when_names_lived_only_in_author_fields(redlined_docx):
    _, report = anonymize(redlined_docx, "Anonymous")

    assert report.leftover_names == []


def test_finds_a_name_hiding_in_a_utf16_embedded_object():
    docx = build_docx(
        {
            "word/document.xml": document(tracked_body()),
            "word/embeddings/oleObject1.bin": "Jane Roe".encode("utf-16-le"),
        }
    )

    _, report = anonymize(docx, "Anonymous")

    assert report.leftover_names == ["Jane Roe"]


def test_reports_a_name_left_in_document_properties_when_not_stripping():
    docx = build_docx(
        {
            "word/document.xml": document(tracked_body()),
            "docProps/core.xml": core_properties(creator="Jane Roe"),
        }
    )

    _, report = anonymize(docx, "Anonymous", strip_metadata=False)

    assert "Jane Roe" in report.leftover_names


def test_leftover_scan_ignores_case():
    body = tracked_body() + "<w:p><w:r><w:t>countersigned by JANE ROE</w:t></w:r></w:p>"
    docx = build_docx({"word/document.xml": document(body)})

    _, report = anonymize(docx, "Anonymous")

    assert report.leftover_names == ["Jane Roe"]


# --------------------------------------------------------------------------
# XML correctness
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "replacement",
    ["Smith & Sons", "Counsel <Redacted>", 'The "Buyer"', "O'Brien & Co <LLP>"],
)
def test_a_replacement_name_with_xml_metacharacters_stays_well_formed(redlined_docx, replacement):
    result, report = anonymize(redlined_docx, replacement)

    ET.fromstring(part(result, "word/comments.xml"))
    ET.fromstring(part(result, "word/document.xml"))
    assert set(report.authors.values()) == {replacement}


def test_an_escaped_author_name_is_matched_and_reported_unescaped():
    docx = build_docx(
        {"word/document.xml": document(tracked_body("Smith &amp; Sons", "Smith &amp; Sons"))}
    )

    result, report = anonymize(docx, "Anonymous")

    assert report.authors == {"Smith & Sons": "Anonymous"}
    assert b"Smith &amp; Sons" not in all_bytes(result)


def test_the_documents_own_text_is_never_touched():
    body = tracked_body() + "<w:p><w:r><w:t>The purchase price is $4,000,000.</w:t></w:r></w:p>"
    docx = build_docx({"word/document.xml": document(body)})

    result, _ = anonymize(docx, "Anonymous")

    assert b"The purchase price is $4,000,000." in part(result, "word/document.xml")


# --------------------------------------------------------------------------
# Archive integrity
# --------------------------------------------------------------------------


def test_output_preserves_part_order_and_leaves_binary_parts_untouched():
    image = bytes(range(256)) * 4
    docx = build_docx(
        {
            "word/document.xml": document(tracked_body()),
            "word/media/image1.png": image,
            "word/comments.xml": comments((1, "Jane Roe", "JR")),
        }
    )

    result, _ = anonymize(docx, "Anonymous")

    assert part_names(result) == part_names(docx)
    assert part(result, "word/media/image1.png") == image


def test_output_is_a_readable_docx_that_can_be_processed_again(redlined_docx):
    once, _ = anonymize(redlined_docx, "First Pass")
    twice, report = anonymize(once, "Second Pass")

    assert report.authors == {"First Pass": "Second Pass"}
    assert b"First Pass" not in all_bytes(twice)


def test_report_serialises_to_json_friendly_primitives(redlined_docx):
    _, report = anonymize(redlined_docx, "Anonymous", distinct=True)
    payload = report.as_dict()

    assert payload["authors"] == [
        {"original": "Jane Roe", "replacement": "Anonymous 1"},
        {"original": "John Doe", "replacement": "Anonymous 2"},
    ]
    assert payload["comment_authors_renamed"] == 2
    assert payload["revision_authors_renamed"] == 3
    assert payload["metadata_cleared"] is True
    assert payload["leftover_names"] == []


def test_the_replacement_name_is_trimmed(redlined_docx):
    _, report = anonymize(redlined_docx, "  Anonymous  ")

    assert set(report.authors.values()) == {"Anonymous"}


# --------------------------------------------------------------------------
# Guarantees the callers rely on
# --------------------------------------------------------------------------


def test_an_author_outside_the_mapping_is_left_byte_identical():
    """The property that makes ``comments_only`` safe: no mapping, no rewrite."""
    xml = b'<w:ins w:author="Mapped"/><w:del w:author="Unmapped"/>'

    rewritten, count = anonymizer._rewrite_authors(xml, {b"Mapped": b"Anonymous"})

    assert rewritten == b'<w:ins w:author="Anonymous"/><w:del w:author="Unmapped"/>'
    assert count == 1


def test_directory_entries_in_the_archive_are_skipped():
    """Some producers write explicit directory entries; they carry no content."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        directory = zipfile.ZipInfo("word/")
        directory.external_attr = 0o40755 << 16
        archive.writestr(directory, b"")
        archive.writestr("word/document.xml", document(tracked_body()))

    result, report = anonymize(buffer.getvalue(), "Anonymous")

    assert report.revision_authors_renamed == 3
    assert part_names(result) == ["word/document.xml"]
