"""A chart's data is a workbook inside the deck or memo: taken, and checked as an upload is.

Found 12 November 2026, with a founder's ten-slide deck: every Office content
type contains "openxmlformats", so the checker took the chart's workbook for
XML, could not parse it, and refused the deck as unreadable. An Office file
inside another is now checked with the same rules, one level deep: it opens
in Excel when an investor clicks Edit Data on the chart.
"""

import io
import zipfile

import pytest

from snug.rendering import ooxml
from tests.office_files import (
    MACRO_MAIN,
    linked_workbook,
    make_docx,
    make_pptx,
    make_xlsx,
    with_chart,
    with_parts,
)


def test_a_deck_with_a_chart_is_taken() -> None:
    deck = with_chart(make_pptx(10), make_xlsx())
    assert ooxml.inspect(deck) == {"format": "pptx", "slides": 10}


def test_a_memo_with_a_chart_is_taken() -> None:
    memo = with_chart(make_docx(), make_xlsx(), folder="word")
    assert ooxml.inspect(memo)["format"] == "docx"


def test_a_binary_part_typed_by_office_is_not_read_as_xml() -> None:
    # Excel's printer settings: an Office type, "openxmlformats" and all, but not XML.
    book = with_parts(
        make_xlsx(),
        {"xl/printerSettings/printerSettings1.bin": b"\x00\x01binary printer settings"},
        (
            '<Default Extension="bin" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.printerSettings"/>',
        ),
    )
    assert ooxml.inspect(book)["format"] == "xlsx"


def test_a_chart_whose_workbook_fetches_another_is_refused() -> None:
    deck = with_chart(make_pptx(), linked_workbook())
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(deck)
    assert refused.value.code == "external"


def test_a_chart_whose_workbook_has_macros_is_refused() -> None:
    deck = with_chart(make_pptx(), make_xlsx(main=MACRO_MAIN))
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(deck)
    assert refused.value.code == "macros"


def test_an_embedded_package_is_checked_whatever_it_is_called() -> None:
    # Named and typed as anything, it is still a zip: Office would open it as one.
    deck = with_chart(
        make_pptx(), linked_workbook(), name="embeddings/data.bin", typed="application/octet-stream"
    )
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(deck)
    assert refused.value.code == "external"


def test_an_embedded_document_is_checked_too() -> None:
    inner = make_docx(
        settings_rels=(
            '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/attachedTemplate" Target="https://files.example/t.dotx" '
            'TargetMode="External"/>',
        )
    )
    deck = with_chart(
        make_pptx(),
        inner,
        name="embeddings/Microsoft_Word_Document.docx",
        typed="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(deck)
    assert refused.value.code == "external"


def test_a_package_inside_an_embedded_package_is_refused() -> None:
    # No real chart needs it, and each level would be another file to unpack.
    nested = with_chart(make_pptx(), with_chart(make_xlsx(), make_xlsx(), folder="xl"))
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(nested)
    assert refused.value.code == "not_office"


def test_a_large_embedded_package_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ooxml, "MAX_EMBEDDED", 100)
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(with_chart(make_pptx(), make_xlsx()))
    assert refused.value.code == "too_large"


def test_a_stamped_deck_keeps_its_chart_byte_for_byte() -> None:
    workbook = make_xlsx()
    deck = with_chart(make_pptx(), workbook)
    stamped = ooxml.stamp(deck, email="priya@northfield.example", date="12 Nov 2026", ref="RZFQ")
    with zipfile.ZipFile(io.BytesIO(stamped)) as package:
        assert package.read("ppt/embeddings/Microsoft_Excel_Worksheet.xlsx") == workbook


# ---------------------------------------------------------------- however it is hidden
# Security review of the change above, 12 November 2026.


def code_of(data: bytes) -> str:
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(data)
    return refused.value.code


def test_a_workbook_typed_as_a_picture_is_checked() -> None:
    deck = with_chart(make_pptx(), linked_workbook(), name="media/image9.png", typed="image/png")
    assert code_of(deck) == "external"


def test_a_workbook_behind_other_bytes_is_checked() -> None:
    # zipfile, like Office, finds a zip from its end, whatever comes first.
    hidden = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + linked_workbook()
    deck = with_parts(
        make_pptx(),
        {"ppt/media/blob.bin": hidden},
        ('<Default Extension="bin" ContentType="application/octet-stream"/>',),
    )
    assert code_of(deck) == "external"


def test_what_a_chart_names_as_its_workbook_must_be_one() -> None:
    assert code_of(with_chart(make_pptx(), b"not a workbook at all")) == "not_office"


def test_an_old_compound_file_is_refused() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    deck = with_parts(
        make_pptx(),
        {"ppt/embeddings/object.bin": ole},
        ('<Default Extension="bin" ContentType="application/octet-stream"/>',),
    )
    assert code_of(deck) == "macros"


def unpacked(data: bytes) -> int:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return sum(entry.file_size for entry in package.infolist())


def test_embedded_files_share_the_uploads_unpacking_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = make_xlsx()
    deck = with_chart(make_pptx(), workbook)
    # Room for the deck itself, and for the workbook on its own, but not both.
    monkeypatch.setattr(ooxml, "MAX_UNPACKED", unpacked(deck) + unpacked(workbook) - 1)
    assert code_of(deck) == "too_large"


def test_how_many_files_may_be_embedded_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ooxml, "MAX_EMBEDDED_PACKAGES", 1)
    deck = with_parts(
        with_chart(make_pptx(), make_xlsx()), {"ppt/embeddings/second.xlsx": make_xlsx()}
    )
    assert code_of(deck) == "too_large"
