"""Excel workbooks in the confined worker: checked on upload, stamped on download.

A workbook is a zip of XML parts. Snug reads only what it must (the content
types, the workbook's list of sheets and the document properties) and leaves
every sheet exactly as uploaded, so formulas, charts and macros survive.
"""

import io
import zipfile
from pathlib import Path

import pytest

from snug.rendering import sandbox, workbook, worker
from tests.test_render_worker import _confined, linux_only
from tests.workbooks import MACRO_MAIN, MAIN, make_xlsx, parts_of

DC = "{http://purl.org/dc/elements/1.1/}"
PROPS = "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}"
VT = "{http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes}"
STAMP = {"email": "priya@northfield.example", "date": "9 Nov 2026", "ref": "L-7Q2K"}


def custom_properties(data: bytes) -> dict[str, str]:
    root = workbook._parse(parts_of(data)["docProps/custom.xml"])
    return {p.get("name", ""): p.findtext(f"{VT}lpwstr", "") for p in root.iter(f"{PROPS}property")}


def comment(data: bytes) -> str:
    return workbook._parse(parts_of(data)["docProps/core.xml"]).findtext(f"{DC}description", "")


# ---------------------------------------------------------------- checking an upload


def test_the_visible_sheets_are_listed_in_order() -> None:
    data = make_xlsx((("Summary", "visible"), ("Workings", "hidden"), ("P&amp;L", "visible")))
    assert workbook.inspect(data) == {"sheets": ["Summary", "P&L"]}


def test_hidden_sheet_names_are_never_listed() -> None:
    """Investors see the list; a founder's hidden sheet should not be named to them."""
    data = make_xlsx((("Model", "visible"), ("Cap table draft", "veryHidden")))
    assert workbook.inspect(data) == {"sheets": ["Model"]}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"%PDF-1.7 not a workbook", "not_xlsx"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 an old .xls, or a password", "not_xlsx"),
        (make_xlsx(main=MACRO_MAIN), "macros"),
        (make_xlsx(extra={"xl/vbaProject.bin": b"\x00" * 16}), "macros"),
        (make_xlsx((("Only", "hidden"),)), "no_sheets"),
        (make_xlsx(workbook_prefix='<!DOCTYPE x [<!ENTITY a "aaaa">]>'), "not_xlsx"),
    ],
)
def test_what_is_not_a_plain_workbook_is_refused(data: bytes, code: str) -> None:
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(data)
    assert refused.value.code == code


def test_a_doctype_is_refused_in_any_encoding() -> None:
    """UTF-16 spells <!DOCTYPE with zero bytes between the letters; the parser still refuses it."""
    hostile = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "aa">]><x>&a;</x>'
    with pytest.raises(workbook.Refused) as refused:
        workbook._parse(hostile.encode("utf-16"))
    assert refused.value.code == "not_xlsx"


def rebuilt(
    data: bytes, changes: dict[str, bytes], *, also: tuple[tuple[str, bytes], ...] = ()
) -> bytes:
    """The same package with some parts replaced, and more appended (duplicates allowed)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            target.writestr(name, changes.get(name, source.read(name)))
        with (
            pytest.warns(UserWarning)
            if any(n in source.namelist() for n, _ in also)
            else _nothing()
        ):
            for name, part in also:
                target.writestr(name, part)
    return out.getvalue()


class _nothing:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


def _types(data: bytes) -> str:
    return parts_of(data)["[Content_Types].xml"].decode()


def test_a_macro_workbook_is_refused_whatever_the_case_of_its_type() -> None:
    """Content types are case-insensitive, so the check is too."""
    plain = make_xlsx()
    shouted = _types(plain).replace(MAIN, "application/vnd.MS-Excel.Sheet.MacroEnabled.Main+XML")
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(rebuilt(plain, {"[Content_Types].xml": shouted.encode()}))
    assert refused.value.code == "macros"


def test_a_decoy_workbook_part_does_not_hide_the_real_one() -> None:
    """The package's own relationship names the workbook Excel opens; Snug checks that one."""
    plain = make_xlsx()
    types = _types(plain).replace(
        f'<Override PartName="/xl/workbook.xml" ContentType="{MAIN}"/>',
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.ms-excel.sheet.macroenabled.main+xml"/>'
        f'<Override PartName="/xl/decoy.xml" ContentType="{MAIN}"/>',
    )
    hostile = rebuilt(
        plain,
        {"[Content_Types].xml": types.encode()},
        also=(("xl/decoy.xml", parts_of(plain)["xl/workbook.xml"]),),
    )
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(hostile)
    assert refused.value.code == "macros"


@pytest.mark.parametrize(
    "addition",
    [
        '<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>',
        '<Default Extension="dat" ContentType="APPLICATION/VND.MS-OFFICE.VBAPROJECT"/>',
        '<Override PartName="/xl/activeX/activeX1.xml" '
        'ContentType="application/vnd.ms-office.activeX+xml"/>',
    ],
)
def test_macro_code_is_refused_however_it_is_typed(addition: str) -> None:
    plain = make_xlsx()
    types = _types(plain).replace("</Types>", addition + "</Types>")
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(rebuilt(plain, {"[Content_Types].xml": types.encode()}))
    assert refused.value.code == "macros"


def test_a_workbook_that_links_to_macro_code_is_refused() -> None:
    plain = make_xlsx()
    rels = (
        parts_of(plain)["xl/_rels/workbook.xml.rels"]
        .decode()
        .replace(
            "</Relationships>",
            '<Relationship Id="rId9" Type="http://schemas.microsoft.com/office/2006/relationships/'
            'vbaProject" Target="code.dat"/></Relationships>',
        )
    )
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(rebuilt(plain, {"xl/_rels/workbook.xml.rels": rels.encode()}))
    assert refused.value.code == "macros"


@pytest.mark.parametrize(
    "also",
    [
        (("xl/workbook.xml", b"<workbook/>"),),  # the same name twice
        (("XL/Workbook.xml", b"<workbook/>"),),  # names that differ only in case
    ],
)
def test_parts_with_clashing_names_are_refused(also: tuple[tuple[str, bytes], ...]) -> None:
    """Two readers could pick different copies of one part, so neither is trusted."""
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(rebuilt(make_xlsx(), {}, also=also))
    assert refused.value.code == "not_xlsx"


def test_a_zip_with_no_workbook_is_refused() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        package.writestr("readme.txt", "hello")
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(out.getvalue())
    assert refused.value.code == "not_xlsx"


def test_too_many_parts_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workbook, "MAX_PARTS", 5)
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(make_xlsx((("A", "visible"), ("B", "visible"))))
    assert refused.value.code == "too_large"


def test_a_zip_bomb_is_refused_before_it_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """A part that would unpack to more than the limit is refused from the zip's index."""
    monkeypatch.setattr(workbook, "MAX_UNPACKED", 64 * 1024)
    bomb = make_xlsx(extra={"xl/media/padding.bin": b"\x00" * (128 * 1024)})
    with pytest.raises(workbook.Refused) as refused:
        workbook.inspect(bomb)
    assert refused.value.code == "too_large"


# ---------------------------------------------------------------- stamping a download


def test_a_download_names_its_reader_and_leaves_every_sheet_alone() -> None:
    original = make_xlsx((("Model", "visible"), ("Workings", "hidden")))
    stamped = workbook.stamp(original, **STAMP)
    before, after = parts_of(original), parts_of(stamped)
    for name in ("xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"):
        assert after[name] == before[name], name
    assert comment(stamped) == (
        "Shared with priya@northfield.example on 9 Nov 2026, link L-7Q2K.\n\nSeries A model"
    )
    assert custom_properties(stamped) == {
        "Shared with": "priya@northfield.example",
        "Shared on": "9 Nov 2026",
        "Snug link": "L-7Q2K",
    }
    assert workbook.inspect(stamped) == {"sheets": ["Model"]}  # still a workbook


def test_the_authors_details_survive_the_stamp() -> None:
    core = parts_of(workbook.stamp(make_xlsx(), **STAMP))["docProps/core.xml"].decode()
    assert "<dc:creator>Ruth Okafor</dc:creator>" in core
    assert 'xsi:type="dcterms:W3CDTF"' in core  # the prefix the attribute names is kept


def test_a_workbook_without_properties_gets_them() -> None:
    stamped = workbook.stamp(make_xlsx(core_comment=None), **STAMP)
    parts = parts_of(stamped)
    assert comment(stamped).startswith("Shared with priya@northfield.example")
    assert custom_properties(stamped)["Snug link"] == "L-7Q2K"
    types = parts["[Content_Types].xml"].decode()
    assert 'PartName="/docProps/core.xml"' in types and 'PartName="/docProps/custom.xml"' in types
    rels = parts["_rels/.rels"].decode()
    assert 'Target="docProps/core.xml"' in rels and 'Target="docProps/custom.xml"' in rels
    workbook._parse(parts["[Content_Types].xml"])  # still well formed
    workbook._parse(parts["_rels/.rels"])


def test_the_founders_own_custom_properties_are_kept_and_ours_replaced() -> None:
    original = make_xlsx(custom={"Project": "Falcon", "Shared with": "someone@else.example"})
    stamped = workbook.stamp(original, **STAMP)
    assert custom_properties(stamped) == {
        "Project": "Falcon",
        "Shared with": "priya@northfield.example",
        "Shared on": "9 Nov 2026",
        "Snug link": "L-7Q2K",
    }


def test_odd_characters_in_the_stamp_are_escaped() -> None:
    stamped = workbook.stamp(make_xlsx(), email="a<b>&c@x.example", date="9 Nov 2026", ref="L-1")
    assert custom_properties(stamped)["Shared with"] == "a<b>&c@x.example"


# ---------------------------------------------------------------- through the worker


@pytest.fixture
def model(tmp_path: Path) -> Path:
    path = tmp_path / "model.xlsx"
    path.write_bytes(make_xlsx((("Summary", "visible"), ("Detail", "visible"))))
    return path


def test_the_worker_inspects_a_workbook(model: Path) -> None:
    assert sandbox.inspect_xlsx(model) == {"sheets": ["Summary", "Detail"]}


def test_the_worker_stamps_a_workbook(model: Path) -> None:
    stamped = sandbox.stamp_xlsx(model, **STAMP, limit=1024 * 1024)
    assert stamped.startswith(b"PK\x03\x04")
    assert custom_properties(stamped)["Shared with"] == "priya@northfield.example"


def test_a_refused_workbook_says_why_in_plain_words(tmp_path: Path) -> None:
    path = tmp_path / "macros.xlsm"
    path.write_bytes(make_xlsx(main=MACRO_MAIN))
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.inspect_xlsx(path)
    assert str(error.value) == (
        "This workbook contains macros. Save it as an Excel Workbook (.xlsx), then upload it again."
    )


def test_a_minimal_workbook_is_stamped_too() -> None:
    """No properties at all, as some tools write a workbook: the worker warms up on one."""
    worker._warm_up_workbook()  # raises if the minimal workbook cannot be read or stamped


@linux_only
def test_a_confined_worker_can_read_and_stamp_a_workbook(tmp_path: Path) -> None:
    """Everything the workbook code needs is loaded before the worker confines itself."""
    data = make_xlsx((("Summary", "visible"),))
    script = (
        f"from snug.rendering import workbook\ndata = {data!r}\n"
        "print(workbook.inspect(data)['sheets'])\n"
        "print(workbook.stamp(data, email='a@b.example', date='1 Jan 2026', ref='L-1')[:2])\n"
    )
    result = _confined(script, tmp_path)
    lines = result.stdout.splitlines()
    assert worker.fully_confined(lines[0]), result.stdout + result.stderr
    assert lines[1:] == ["['Summary']", "b'PK'"], result.stdout + result.stderr


@pytest.mark.parametrize("output", [b'{"sheets": []}', b'{"sheets": [1]}', b"not json"])
def test_odd_answers_from_the_worker_are_refused(
    model: Path, monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: output)
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.inspect_xlsx(model)
    assert "Excel workbook" in str(error.value)


@pytest.mark.parametrize("output", [b"%PDF-1.7 not a workbook", b"PK\x03\x04" + b"x" * 2048])
def test_a_stamped_workbook_that_is_not_one_or_too_big_is_refused(
    model: Path, monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: output)
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.stamp_xlsx(model, **STAMP, limit=1024)
    assert "Excel workbook" in str(error.value)


def test_a_worker_failure_while_stamping_speaks_of_a_workbook(
    model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> bytes:
        raise sandbox.WorkerError("timeout")

    monkeypatch.setattr(sandbox, "run", fail)
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.stamp_xlsx(model, **STAMP, limit=1024 * 1024)
    assert str(error.value) == "This workbook took too long to read."
