"""Small real .xlsx files for tests, built from their parts with the standard library."""

import io
import zipfile

MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
MACRO_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
CORE = "application/vnd.openxmlformats-package.core-properties+xml"
CUSTOM = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
REL_SHEET = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"

SHEET_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<sheetData><row r="1"><c r="A1"><f>SUM(1,2)</f><v>3</v></c></row></sheetData></worksheet>'
)


def make_xlsx(
    sheets: tuple[tuple[str, str], ...] = (("Model", "visible"),),
    *,
    main: str = MAIN,
    core_comment: str | None = "Series A model",
    custom: dict[str, str] | None = None,
    extra: dict[str, bytes] | None = None,
    workbook_prefix: str = "",
) -> bytes:
    """A workbook with these sheets (name, state), and optional property parts."""
    types = [
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        f'<Override PartName="/xl/workbook.xml" ContentType="{main}"/>',
    ]
    rels = [f'<Relationship Id="rId1" Type="{REL_DOC}" Target="xl/workbook.xml"/>']
    parts: dict[str, bytes] = {}
    if core_comment is not None:
        types.append(f'<Override PartName="/docProps/core.xml" ContentType="{CORE}"/>')
        rels.append(f'<Relationship Id="rId2" Type="{REL_CORE}" Target="docProps/core.xml"/>')
        parts["docProps/core.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/'
            'dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:creator>Ruth Okafor</dc:creator>"
            + (f"<dc:description>{core_comment}</dc:description>" if core_comment else "")
            + '<dcterms:created xsi:type="dcterms:W3CDTF">2026-11-01T09:00:00Z</dcterms:created>'
            "</cp:coreProperties>"
        ).encode()
    if custom is not None:
        types.append(f'<Override PartName="/docProps/custom.xml" ContentType="{CUSTOM}"/>')
        rels.append(f'<Relationship Id="rId3" Type="{REL_CUSTOM}" Target="docProps/custom.xml"/>')
        properties = "".join(
            '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" '
            f'pid="{number}" name="{name}"><vt:lpwstr>{value}</vt:lpwstr></property>'
            for number, (name, value) in enumerate(custom.items(), start=2)
        )
        parts["docProps/custom.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/docPropsVTypes">{properties}</Properties>'
        ).encode()
    sheet_entries, sheet_rels = [], []
    for number, (name, state) in enumerate(sheets, start=1):
        types.append(
            f'<Override PartName="/xl/worksheets/sheet{number}.xml" ContentType="{SHEET}"/>'
        )
        hidden = "" if state == "visible" else f' state="{state}"'
        sheet_entries.append(
            f'<sheet name="{name}" sheetId="{number}"{hidden} r:id="rId{number}"/>'
        )
        sheet_rels.append(
            f'<Relationship Id="rId{number}" Type="{REL_SHEET}" '
            f'Target="worksheets/sheet{number}.xml"/>'
        )
        parts[f"xl/worksheets/sheet{number}.xml"] = SHEET_XML.encode()
    parts["xl/workbook.xml"] = (
        workbook_prefix + '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(sheet_entries)}</sheets></workbook>"
    ).encode()
    parts["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(sheet_rels)
        + "</Relationships>"
    ).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + "".join(types)
            + "</Types>",
        )
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>",
        )
        for name, data in parts.items():
            package.writestr(name, data)
        for name, data in (extra or {}).items():
            package.writestr(name, data)
    return out.getvalue()


def parts_of(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return {name: package.read(name) for name in package.namelist()}
