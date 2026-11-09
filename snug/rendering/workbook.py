"""Excel workbooks (.xlsx), read and stamped inside the confined worker.

A workbook is a zip of XML parts. Snug reads only three of them: the content
types, to know it is a plain workbook without macros; the workbook part, for
the names of its visible sheets; and the document properties, which it
rewrites on each download to name the reader. Every sheet, chart, formula
and style is copied through unchanged, so the investor gets the working file.

Standard library only. The parser refuses any part that declares a DOCTYPE
or an entity, in any encoding, so no entity can be defined or expanded, and the zip's index is
checked against limits before anything is unpacked. The worker's own memory
and CPU limits stand behind both.
"""

import io
import posixpath
import shutil
import zipfile
from xml.etree import ElementTree
from xml.parsers import expat

MAX_PARTS = 10_000
# What the zip's index says it unpacks to, at most. zipfile never unpacks more
# than a part's stated size, so a lying index cannot get past this.
MAX_UNPACKED = 1024 * 1024 * 1024
# The XML parts Snug parses are small; anything bigger is not a real workbook.
MAX_PARSED_PART = 16 * 1024 * 1024

CONTENT_TYPES = "[Content_Types].xml"
ROOT_RELS = "_rels/.rels"
CORE_PART = "docProps/core.xml"
CUSTOM_PART = "docProps/custom.xml"

MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
# A content type or relationship naming any of these is code Excel could run.
CODE_WORDS = ("macroenabled", "vbaproject", "activex", "vbadata")
REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
REL_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"

NS_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_DCTERMS = "http://purl.org/dc/terms/"
NS_DCMITYPE = "http://purl.org/dc/dcmitype/"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
NS_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
# Custom properties' format id, the same for every user-defined property.
FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
OUR_PROPERTIES = ("Shared with", "Shared on", "Snug link")


class Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _package(data: bytes) -> zipfile.ZipFile:
    try:
        package = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as error:
        raise Refused("not_xlsx") from error
    entries = package.infolist()
    if len(entries) > MAX_PARTS or sum(e.file_size for e in entries) > MAX_UNPACKED:
        raise Refused("too_large")
    if any(e.flag_bits & 0x1 for e in entries):  # an encrypted part
        raise Refused("not_xlsx")
    return package


def _xml(package: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        info = package.getinfo(name)
    except KeyError as error:
        raise Refused("not_xlsx") from error
    if info.file_size > MAX_PARSED_PART:
        raise Refused("too_large")
    return _parse(package.read(info))


def _parse(raw: bytes) -> ElementTree.Element:
    """Parse one part, refusing any DOCTYPE or entity declaration, in whatever encoding.

    The parser itself refuses them (as defusedxml does), since a byte search
    would miss a declaration spelled in UTF-16. With no DTD, no entity can be
    defined, so none can be expanded or fetched.
    """

    def refuse(*_: object) -> None:
        raise Refused("not_xlsx")

    def refuse_reference(*_: object) -> int:
        raise Refused("not_xlsx")

    def name(raw_name: str) -> str:
        # expat gives "uri}local"; ElementTree spells it "{uri}local".
        return "{" + raw_name if "}" in raw_name else raw_name

    builder = ElementTree.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse_reference
    parser.StartElementHandler = lambda tag, attributes: builder.start(
        name(tag), {name(key): value for key, value in attributes.items()}
    )
    parser.EndElementHandler = lambda tag: builder.end(name(tag))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(raw, True)
    except expat.ExpatError as error:
        raise Refused("not_xlsx") from error
    return builder.close()


def _workbook_part(package: zipfile.ZipFile) -> str:
    """The workbook part Excel would open, refusing anything with macros or ActiveX.

    An allowlist, read the way Office reads a package: the root relationship
    names the workbook part, and that part's content type (from its override,
    or the default for its extension, compared without regard to case, as
    content types are) must be a plain workbook's. Any part typed as macro or
    ActiveX code, or a workbook relationship to such code, is refused, as is a
    package with two parts whose names differ only in case, since two readers
    could each pick a different one.
    """
    names = package.namelist()
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise Refused("not_xlsx")
    overrides: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for entry in _xml(package, CONTENT_TYPES):
        kind = entry.get("ContentType", "").casefold()
        if entry.tag == f"{{{NS_TYPES}}}Override":
            overrides[entry.get("PartName", "").lstrip("/").casefold()] = kind
        elif entry.tag == f"{{{NS_TYPES}}}Default":
            defaults[entry.get("Extension", "").casefold()] = kind
    for kind in (*overrides.values(), *defaults.values()):
        if any(word in kind for word in CODE_WORDS):
            raise Refused("macros")
    if any(name.endswith("vbaproject.bin") for name in folded):
        raise Refused("macros")

    targets = [
        relation.get("Target", "")
        for relation in _xml(package, ROOT_RELS).iter(f"{{{NS_RELS}}}Relationship")
        if relation.get("Type") == REL_DOC
    ]
    if len(targets) != 1:
        raise Refused("not_xlsx")
    main = posixpath.normpath(targets[0].lstrip("/")).casefold()
    extension = main.rpartition(".")[2]
    if overrides.get(main, defaults.get(extension)) != MAIN.casefold() or main not in folded:
        raise Refused("not_xlsx")
    part = names[folded.index(main)]

    folder, _, base = part.rpartition("/")
    rels = f"{folder}/_rels/{base}.rels" if folder else f"_rels/{base}.rels"
    if rels.casefold() in folded:
        for relation in _xml(package, names[folded.index(rels.casefold())]).iter(
            f"{{{NS_RELS}}}Relationship"
        ):
            if any(word in relation.get("Type", "").casefold() for word in CODE_WORDS):
                raise Refused("macros")
    return part


def inspect(data: bytes) -> dict[str, list[str]]:
    """The names of the workbook's visible sheets, in order."""
    with _package(data) as package:
        part = _xml(package, _workbook_part(package))
    sheets = [
        sheet.get("name", "")
        for sheet in part.iter(f"{{{NS_MAIN}}}sheet")
        if sheet.get("state", "visible") == "visible"
    ]
    if not sheets:
        raise Refused("no_sheets")
    return {"sheets": sheets}


def _serialise(element: ElementTree.Element, prefixes: dict[str, str]) -> bytes:
    # Each part keeps the prefixes Office expects; an attribute such as
    # xsi:type="dcterms:W3CDTF" names a prefix, which must not change.
    for prefix, uri in prefixes.items():
        ElementTree.register_namespace(prefix, uri)
    body = ElementTree.tostring(element, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body).encode()


def _core(package: zipfile.ZipFile, line: str) -> bytes:
    if CORE_PART in package.namelist():
        root = _xml(package, CORE_PART)
    else:
        root = ElementTree.Element(f"{{{NS_CP}}}coreProperties")
    description = root.find(f"{{{NS_DC}}}description")
    if description is None:
        description = ElementTree.SubElement(root, f"{{{NS_DC}}}description")
    earlier = (description.text or "").strip()
    description.text = f"{line}\n\n{earlier}" if earlier else line
    prefixes = {
        "cp": NS_CP,
        "dc": NS_DC,
        "dcterms": NS_DCTERMS,
        "dcmitype": NS_DCMITYPE,
        "xsi": NS_XSI,
    }
    return _serialise(root, prefixes)


def _custom(package: zipfile.ZipFile, values: dict[str, str]) -> bytes:
    if CUSTOM_PART in package.namelist():
        root = _xml(package, CUSTOM_PART)
    else:
        root = ElementTree.Element(f"{{{NS_PROPS}}}Properties")
    for old in list(root.iter(f"{{{NS_PROPS}}}property")):
        if old.get("name") in values:
            root.remove(old)
    numbers = [int(p.get("pid", "1")) for p in root.iter(f"{{{NS_PROPS}}}property")]
    pid = max([1, *numbers]) + 1  # user-defined properties start at 2
    for offset, (name, value) in enumerate(values.items()):
        prop = ElementTree.SubElement(
            root,
            f"{{{NS_PROPS}}}property",
            {"fmtid": FMTID, "pid": str(pid + offset), "name": name},
        )
        ElementTree.SubElement(prop, f"{{{NS_VT}}}lpwstr").text = value
    return _serialise(root, {"": NS_PROPS, "vt": NS_VT})


def _with_child(raw: bytes, namespace: str, tag: str, attributes: dict[str, str]) -> bytes:
    """A small package part (content types or relationships) with one more entry."""
    root = _parse(raw)
    ElementTree.SubElement(root, f"{{{namespace}}}{tag}", attributes)
    return _serialise(root, {"": namespace})


def stamp(data: bytes, *, email: str, date: str, ref: str) -> bytes:
    """The same workbook, with its properties naming the reader. Sheets are copied unchanged."""
    line = f"Shared with {email} on {date}, link {ref}."
    values = dict(zip(OUR_PROPERTIES, (email, date, ref), strict=True))
    with _package(data) as package:
        _workbook_part(package)  # a plain workbook, without macros
        names = package.namelist()
        replaced = {CORE_PART: _core(package, line), CUSTOM_PART: _custom(package, values)}
        if ROOT_RELS not in names:
            raise Refused("not_xlsx")
        types_raw = package.read(CONTENT_TYPES)
        rels_raw = package.read(ROOT_RELS)
        added = [
            (part, kind, rel)
            for part, kind, rel in (
                (CORE_PART, CORE_TYPE, REL_CORE),
                (CUSTOM_PART, CUSTOM_TYPE, REL_CUSTOM),
            )
            if part not in names
        ]
        for number, (part, kind, rel) in enumerate(added, start=1):
            override = {"PartName": f"/{part}", "ContentType": kind}
            types_raw = _with_child(types_raw, NS_TYPES, "Override", override)
            relation = {"Id": f"rIdSnug{number}", "Type": rel, "Target": part}
            rels_raw = _with_child(rels_raw, NS_RELS, "Relationship", relation)
        replaced[CONTENT_TYPES] = types_raw
        replaced[ROOT_RELS] = rels_raw

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
            for info in package.infolist():
                entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = info.external_attr
                if info.filename in replaced:
                    copy.writestr(entry, replaced[info.filename])
                    continue
                # Streamed, so a large sheet never sits in memory whole.
                with package.open(info) as source, copy.open(entry, "w") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
            for part, _kind, _rel in added:
                copy.writestr(part, replaced[part])
    return out.getvalue()
