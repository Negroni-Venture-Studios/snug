"""Small real .xlsx, .docx and .pptx files for tests, built from their parts."""

import io
import zipfile

MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
MACRO_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
DOCM_MAIN = "application/vnd.ms-word.document.macroEnabled.main+xml"
PPTX_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
PPTM_MAIN = "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"
SLIDE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
SETTINGS = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
REL_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
REL_SETTINGS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
REL_TEMPLATE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"
)
REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
REL_HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
RELS_OPEN = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'


def relationship(rid: str, kind: str, target: str, *, external: bool = False) -> str:
    mode = ' TargetMode="External"' if external else ""
    return f'<Relationship Id="{rid}" Type="{kind}" Target="{target}"{mode}/>'


def rels_part(*relationships: str) -> bytes:
    return (XML_HEAD + RELS_OPEN + "".join(relationships) + "</Relationships>").encode()


def _zip(types: list[str], rels: list[str], parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            XML_HEAD
            + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            + "".join(types)
            + "</Types>",
        )
        package.writestr("_rels/.rels", rels_part(*rels))
        for name, data in parts.items():
            package.writestr(name, data)
    return out.getvalue()


def make_docx(
    *,
    main: str = DOCX_MAIN,
    settings_rels: tuple[str, ...] = (),
    document_rels: tuple[str, ...] = (),
    extra: dict[str, bytes] | None = None,
    body: str = "<w:p><w:r><w:t>Board memo, November 2026</w:t></w:r></w:p>",
    extra_types: tuple[str, ...] = (),
) -> bytes:
    """A Word document with this body, and relationships on its settings or body if given."""
    types = [
        f'<Override PartName="/word/document.xml" ContentType="{main}"/>',
        f'<Override PartName="/word/settings.xml" ContentType="{SETTINGS}"/>',
        *extra_types,
    ]
    parts = {
        "word/document.xml": (
            f'{XML_HEAD}<w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}">'
            f"<w:body>{body}</w:body></w:document>"
        ).encode(),
        "word/settings.xml": f'{XML_HEAD}<w:settings xmlns:w="{NS_W}"/>'.encode(),
        "word/_rels/document.xml.rels": rels_part(
            relationship("rId1", REL_SETTINGS, "settings.xml"), *document_rels
        ),
        **(extra or {}),
    }
    if settings_rels:
        parts["word/_rels/settings.xml.rels"] = rels_part(*settings_rels)
    rels = [relationship("rId1", REL_DOC, "word/document.xml")]
    return _zip(types, rels, parts)


PRESENTATIONML = "application/vnd.openxmlformats-officedocument.presentationml"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
GROUP = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
THEME = (
    f'{XML_HEAD}<a:theme xmlns:a="{NS_A}" name="Snug"><a:themeElements>'
    '<a:clrScheme name="Snug">'
    '<a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="1A0B00"/></a:dk2><a:lt2><a:srgbClr val="FFE2CE"/></a:lt2>'
    + "".join(f'<a:accent{n}><a:srgbClr val="FF6200"/></a:accent{n}>' for n in range(1, 7))
    + '<a:hlink><a:srgbClr val="169C59"/></a:hlink><a:folHlink><a:srgbClr val="127D47"/>'
    "</a:folHlink></a:clrScheme>"
    '<a:fontScheme name="Snug">'
    + "".join(
        f'<a:{kind}><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:{kind}>'
        for kind in ("majorFont", "minorFont")
    )
    + '</a:fontScheme><a:fmtScheme name="Snug">'
    + "<a:fillStyleLst>"
    + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + "</a:fillStyleLst><a:lnStyleLst>"
    + '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3
    + "</a:lnStyleLst><a:effectStyleLst>"
    + "<a:effectStyle><a:effectLst/></a:effectStyle>" * 3
    + "</a:effectStyleLst><a:bgFillStyleLst>"
    + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + "</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>"
)


def _title(number: int) -> str:
    """A slide's spTree with one text box, as PowerPoint writes a title."""
    return (
        f'<p:spTree>{GROUP}<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/>'
        '<p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="457200" y="457200"/>'
        '<a:ext cx="8229600" cy="1143000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
        "</a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>"
        f'<a:rPr lang="en-GB" sz="3200"/><a:t>Slide {number}</a:t></a:r></a:p></p:txBody>'
        "</p:sp></p:spTree>"
    )


def make_pptx(
    slides: int = 3,
    *,
    main: str = PPTX_MAIN,
    slide_rels: tuple[str, ...] = (),
    slide_body: str | None = None,
) -> bytes:
    """A presentation as PowerPoint writes one: a master, a layout, a theme and titled slides.

    The first slide has `slide_rels` beside its layout, and `slide_body` in
    place of its title, if given.
    """
    names = f'xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"'
    types = [
        f'<Override PartName="/ppt/presentation.xml" ContentType="{main}"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
        f'ContentType="{PRESENTATIONML}.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
        f'ContentType="{PRESENTATIONML}.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
    ]
    layout = relationship("rIdLayout", f"{REL}/slideLayout", "../slideLayouts/slideLayout1.xml")
    ids, links = (
        [],
        [
            relationship("rIdMaster", f"{REL}/slideMaster", "slideMasters/slideMaster1.xml"),
            relationship("rIdTheme", f"{REL}/theme", "theme/theme1.xml"),
        ],
    )
    parts: dict[str, bytes] = {}
    for number in range(1, slides + 1):
        types.append(f'<Override PartName="/ppt/slides/slide{number}.xml" ContentType="{SLIDE}"/>')
        ids.append(f'<p:sldId id="{255 + number}" r:id="rId{number}"/>')
        links.append(relationship(f"rId{number}", REL_SLIDE, f"slides/slide{number}.xml"))
        shapes = slide_body if number == 1 and slide_body is not None else _title(number)
        parts[f"ppt/slides/slide{number}.xml"] = (
            f"{XML_HEAD}<p:sld {names}><p:cSld>{shapes}</p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
        ).encode()
        extra = slide_rels if number == 1 else ()
        parts[f"ppt/slides/_rels/slide{number}.xml.rels"] = rels_part(layout, *extra)
    parts["ppt/presentation.xml"] = (
        f"{XML_HEAD}<p:presentation {names}><p:sldMasterIdLst>"
        '<p:sldMasterId id="2147483648" r:id="rIdMaster"/></p:sldMasterIdLst>'
        f"<p:sldIdLst>{''.join(ids)}</p:sldIdLst>"
        '<p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    ).encode()
    parts["ppt/_rels/presentation.xml.rels"] = rels_part(*links)
    parts["ppt/slideMasters/slideMaster1.xml"] = (
        f"{XML_HEAD}<p:sldMaster {names}><p:cSld><p:spTree>{GROUP}</p:spTree></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
        'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" '
        'folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rIdLayout"/>'
        "</p:sldLayoutIdLst></p:sldMaster>"
    ).encode()
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = rels_part(
        relationship("rIdLayout", f"{REL}/slideLayout", "../slideLayouts/slideLayout1.xml"),
        relationship("rIdTheme", f"{REL}/theme", "../theme/theme1.xml"),
    )
    parts["ppt/slideLayouts/slideLayout1.xml"] = (
        f'{XML_HEAD}<p:sldLayout {names} type="blank"><p:cSld name="Blank"><p:spTree>{GROUP}'
        "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    ).encode()
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = rels_part(
        relationship("rIdMaster", f"{REL}/slideMaster", "../slideMasters/slideMaster1.xml")
    )
    parts["ppt/theme/theme1.xml"] = THEME.encode()
    rels = [relationship("rId1", REL_DOC, "ppt/presentation.xml")]
    return _zip(types, rels, parts)


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
