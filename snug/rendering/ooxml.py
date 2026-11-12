"""Office files (.xlsx, .docx and .pptx), read and stamped inside the confined worker.

An Office file is a zip of XML parts. Snug reads only what it must: the
content types, to know it is a plain workbook, document or presentation
without macros; every part's relationships, to refuse code and anything
fetched from another computer when the file is opened; the main part, for a
workbook's visible sheets or a presentation's slide count; and the document
properties, which it rewrites on each download to name the reader. Every
sheet, slide, paragraph and picture is copied through unchanged, so the
investor gets the working file.

Standard library only. The parser refuses any part that declares a DOCTYPE
or an entity, in any encoding, so no entity can be defined or expanded, and the zip's index is
checked against limits before anything is unpacked. The worker's own memory
and CPU limits stand behind both.
"""

import io
import posixpath
import re
import shutil
import zipfile
from collections.abc import Callable
from xml.etree import ElementTree
from xml.parsers import expat

MAX_PARTS = 10_000
# What the zip's index says it unpacks to, at most. zipfile never unpacks more
# than a part's stated size, so a lying index cannot get past this.
MAX_UNPACKED = 1024 * 1024 * 1024
# The XML parts Snug parses are small; anything bigger is not a real Office file.
MAX_PARSED_PART = 16 * 1024 * 1024
MAX_SLIDES = 10_000

CONTENT_TYPES = "[Content_Types].xml"
ROOT_RELS = "_rels/.rels"
CORE_PART = "docProps/core.xml"
CUSTOM_PART = "docProps/custom.xml"

MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
PPTX_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
# The main part's content type says which format a package is. Templates,
# slide shows and macro-enabled files are none of these, so they are refused.
FORMATS = {MAIN: "xlsx", DOCX_MAIN: "docx", PPTX_MAIN: "pptx"}
# A content type or relationship naming any of these is code Office could run:
# VBA projects, Excel 4.0 macro sheets (and their international form),
# ActiveX controls, and embedded OLE objects. A real file needs none of them.
CODE_WORDS = ("macroenabled", "vbaproject", "vbadata", "macrosheet", "activex", "oleobject")
REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
# Only a hyperlink may point at another computer: it is followed when clicked,
# never when the file opens. Anything else external (a template, a frame, a
# linked picture or object) is fetched on opening, so must be on the author's disk.
REL_HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
# The elements that follow a hyperlink only when it is clicked. A hyperlink's
# relationship may point anywhere, so no other element may use it.
HYPERLINK_ELEMENTS = frozenset({"hyperlink", "hlinkClick", "hlinkHover", "hlinkMouseOver"})
# Word fields that fetch content when they update, and the ones that start another program.
FETCHING_FIELDS = frozenset({"INCLUDEPICTURE", "INCLUDETEXT", "INCLUDE", "LINK", "IMPORT"})
DDE_FIELDS = frozenset({"DDE", "DDEAUTO"})
# Excel's links to another program, in an external link part.
DDE_ELEMENTS = frozenset({"ddeLink", "oleLink"})
CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
REL_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"

NS_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_PRESENTATION = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
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
        raise Refused("not_office") from error
    entries = package.infolist()
    if len(entries) > MAX_PARTS or sum(e.file_size for e in entries) > MAX_UNPACKED:
        raise Refused("too_large")
    if any(e.flag_bits & 0x1 for e in entries):  # an encrypted part
        raise Refused("not_office")
    return package


def _xml(package: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        info = package.getinfo(name)
    except KeyError as error:
        raise Refused("not_office") from error
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
        raise Refused("not_office")

    def refuse_reference(*_: object) -> int:
        raise Refused("not_office")

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
        raise Refused("not_office") from error
    return builder.close()


_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]+):", re.IGNORECASE)


def _remote(target: str) -> bool:
    """True if a relationship's target names another computer.

    A path on a local disk (C:\\..., file:///C:/..., or relative) is looked
    for on the reader's own computer, never fetched. A URL with any scheme
    but file, a UNC path, or a file URL with a host is fetched from elsewhere.
    A one-letter scheme is a drive letter, not a scheme.
    """
    target = target.strip().replace("\\", "/")
    if target.startswith("//"):
        return True
    scheme = _SCHEME.match(target)
    if scheme is None:
        return False
    if scheme.group(1).casefold() != "file":
        return True
    rest = target[len(scheme.group(0)) :]
    # file:///C:/... has an empty host; file://server/... and file:////server/... do not.
    return (rest.startswith("//") and not rest.startswith("///")) or rest.startswith("////")


def _main_part(package: zipfile.ZipFile) -> tuple[str, str]:
    """The part Office would open, and its format, refusing macros, ActiveX and remote content.

    An allowlist, read the way Office reads a package: the root relationship
    names the main part, and that part's content type (from its override, or
    the default for its extension, compared without regard to case, as
    content types are) must be a plain workbook's, document's or
    presentation's. Any part typed as macro or ActiveX code, or a relationship
    to such code, is refused, as is a package with two parts whose names
    differ only in case, since two readers could each pick a different one.
    So is any relationship other than a hyperlink that points at another
    computer: Office would fetch it when the file opens.
    """
    names = package.namelist()
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise Refused("not_office")
    overrides: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for entry in _xml(package, CONTENT_TYPES):
        kind = entry.get("ContentType", "").casefold()
        # Each type is checked as it is read, so no later entry can hide it.
        if any(word in kind for word in CODE_WORDS):
            raise Refused("macros")
        if entry.tag == f"{{{NS_TYPES}}}Override":
            table, key = overrides, entry.get("PartName", "").lstrip("/").casefold()
        elif entry.tag == f"{{{NS_TYPES}}}Default":
            table, key = defaults, entry.get("Extension", "").casefold()
        else:
            continue
        if key in table:  # typed twice: two readers could believe different types
            raise Refused("not_office")
        table[key] = kind
    if any(name.endswith("vbaproject.bin") for name in folded):
        raise Refused("macros")
    # Every part's relationships, not only the main part's: a sheet or slide can link to code too.
    hyperlinks: dict[str, set[str]] = {}  # part name -> ids of its hyperlinks to other computers
    for name in names:
        if name.casefold().endswith(".rels"):
            for relation in _xml(package, name).iter(f"{{{NS_RELS}}}Relationship"):
                kind = relation.get("Type", "")
                if any(word in kind.casefold() for word in CODE_WORDS):
                    raise Refused("macros")
                external = relation.get("TargetMode", "").casefold() == "external"
                if not external or not _remote(relation.get("Target", "")):
                    continue
                if kind != REL_HYPERLINK:
                    raise Refused("external")
                hyperlinks.setdefault(_source_of(name), set()).add(relation.get("Id", ""))

    targets = [
        relation.get("Target", "")
        for relation in _xml(package, ROOT_RELS).iter(f"{{{NS_RELS}}}Relationship")
        if relation.get("Type") == REL_DOC
    ]
    if len(targets) != 1:
        raise Refused("not_office")
    main = posixpath.normpath(targets[0].lstrip("/")).casefold()
    extension = main.rpartition(".")[2]
    found = {kind.casefold(): name for kind, name in FORMATS.items()}.get(
        overrides.get(main, defaults.get(extension, ""))
    )
    if found is None or main not in folded:
        raise Refused("not_office")
    xml_parts = [
        name
        for name, folded_name in zip(names, folded, strict=True)
        if not folded_name.endswith(".rels")
        and name != CONTENT_TYPES
        and "xml" in overrides.get(folded_name, defaults.get(folded_name.rpartition(".")[2], ""))
    ]
    _check_content(package, xml_parts, hyperlinks)
    return names[folded.index(main)], found


def _source_of(rels: str) -> str:
    """The part a relationships part belongs to: a/_rels/b.xml.rels is a/b.xml."""
    folder, name = posixpath.split(rels)
    return posixpath.join(posixpath.dirname(folder), name.removesuffix(".rels"))


def _stream(
    package: zipfile.ZipFile,
    name: str,
    start: Callable[[str, dict[str, str]], None],
    *,
    end: Callable[[str], None] | None = None,
    text: Callable[[str], None] | None = None,
) -> None:
    """Read one part in pieces, calling back on each element, refusing DTDs as _parse does.

    No tree is built and the part is never held whole, so a large sheet can be
    checked within the worker's memory. Names reach the callbacks as "uri}local".
    """

    def refuse(*_: object) -> None:
        raise Refused("not_office")

    def refuse_reference(*_: object) -> int:
        raise Refused("not_office")

    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse_reference
    parser.StartElementHandler = start
    if end is not None:
        parser.EndElementHandler = end
    if text is not None:
        parser.CharacterDataHandler = text
    try:
        with package.open(name) as source:
            while chunk := source.read(1024 * 1024):
                parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError as error:
        raise Refused("not_office") from error


def _local(name: str) -> str:
    return name.rpartition("}")[2]


_FIELD_WORDS = re.compile(r'"([^"]*)"|(\S+)')
# A field switch is a backslash and one character, such as \d or \*; anything
# else starting with a backslash, such as \\server\share, is an argument.
_SWITCH = re.compile(r"^\\[A-Za-z*#@!]$")
# A keyword already written in full, before any nested field: " IF ", not " INCLUDE".
_KEYWORD = re.compile(r"\s*(\S+)\s")


def _check_keyword(keyword: str) -> None:
    if keyword.upper() in DDE_FIELDS:
        raise Refused("dde")


def _check_field(instruction: str) -> None:
    """Refuse a Word field that starts another program, or fetches from another computer."""
    words = _FIELD_WORDS.findall(instruction)  # (quoted, bare) pairs; one of each is empty
    if not words:
        return
    keyword = "".join(words[0]).upper()
    _check_keyword(keyword)
    arguments = [quoted or bare for quoted, bare in words[1:] if quoted or not _SWITCH.match(bare)]
    if keyword in FETCHING_FIELDS and any(_remote(argument) for argument in arguments):
        raise Refused("external")


class _Field:
    """One Word field while it is read: its instruction so far, and what is known of it."""

    def __init__(self) -> None:
        self.instruction: list[str] = []
        self.reading = True  # still in its instruction, before its separate mark
        self.nested = False  # its instruction holds another field, so its words are not final


def _open_field(fields: list[_Field]) -> None:
    """A field begins. Inside another field's instruction, that field's keyword must be settled.

    { INCLUDEPICTURE "{ QUOTE "http://..." }" } builds its address from a
    nested field, and { { QUOTE "INCLUDE" }PICTURE ... } its keyword, so
    neither can be read. A field with something nested in its instruction is
    allowed only if its keyword is already written in full and is one that
    never fetches or starts anything, such as IF in a mail merge.
    """
    if fields and fields[-1].reading:
        written = _KEYWORD.match("".join(fields[-1].instruction))
        if written is None:
            raise Refused("field")
        keyword = written.group(1).upper()
        _check_keyword(keyword)
        if keyword in FETCHING_FIELDS:
            raise Refused("external")
        fields[-1].nested = True
    fields.append(_Field())


def _close_instruction(field: _Field) -> None:
    if field.reading:
        field.reading = False
        if not field.nested:  # a nested field's outer keyword was checked when it opened
            _check_field("".join(field.instruction))


def _check_part(package: zipfile.ZipFile, name: str, ids: set[str]) -> None:
    """One XML part, read once in a stream, for everything its relationships cannot show.

    - Word fields, simple or written across runs between fldChar marks.
    - Excel's DDE and OLE links, which start or reach another program.
    - Any use of a relationship typed as a hyperlink (`ids`) by something
      other than a hyperlink, in an attribute of any namespace.
    """
    fields: list[_Field] = []
    in_instruction = [False]

    def start(tag: str, attributes: dict[str, str]) -> None:
        local = _local(tag)
        if local not in HYPERLINK_ELEMENTS and ids and not ids.isdisjoint(attributes.values()):
            raise Refused("external")
        if local in DDE_ELEMENTS:
            raise Refused("dde")
        if not tag.startswith(f"{NS_W}}}"):
            return
        if local == "fldSimple":
            _check_field(attributes.get(f"{NS_W}}}instr", ""))
        elif local == "instrText":
            in_instruction[0] = True
        elif local == "fldChar":
            kind = attributes.get(f"{NS_W}}}fldCharType", "")
            if kind == "begin":
                _open_field(fields)
            elif kind in ("separate", "end") and fields:
                _close_instruction(fields[-1])
                if kind == "end":
                    fields.pop()

    def end(tag: str) -> None:
        if tag == f"{NS_W}}}instrText":
            in_instruction[0] = False

    def text(data: str) -> None:
        if in_instruction[0] and fields and fields[-1].reading:
            fields[-1].instruction.append(data)

    _stream(package, name, start, end=end, text=text)
    for field in fields:  # a field never closed is still checked
        _close_instruction(field)


def _check_content(
    package: zipfile.ZipFile, xml_parts: list[str], hyperlinks: dict[str, set[str]]
) -> None:
    """Every XML part, whatever its folder: Office finds parts by relationship, not by path."""
    by_folded = {source.casefold(): ids for source, ids in hyperlinks.items()}
    for name in xml_parts:
        _check_part(package, name, by_folded.get(name.casefold(), set()))


def inspect(data: bytes) -> dict[str, object]:
    """Which format the file is, with a workbook's visible sheets or a presentation's slides."""
    with _package(data) as package:
        name, found = _main_part(package)
        if found == "docx":
            return {"format": "docx"}
        part = _xml(package, name)
    if found == "pptx":
        slides = sum(1 for _ in part.iter(f"{{{NS_PRESENTATION}}}sldId"))
        if not slides:
            raise Refused("no_slides")
        if slides > MAX_SLIDES:
            raise Refused("too_large")
        return {"format": "pptx", "slides": slides}
    sheets = [
        sheet.get("name", "")
        for sheet in part.iter(f"{{{NS_MAIN}}}sheet")
        if sheet.get("state", "visible") == "visible"
    ]
    if not sheets:
        raise Refused("no_sheets")
    return {"format": "xlsx", "sheets": sheets}


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
    """The same file, with its properties naming the reader. Everything else is copied unchanged."""
    line = f"Shared with {email} on {date}, link {ref}."
    values = dict(zip(OUR_PROPERTIES, (email, date, ref), strict=True))
    with _package(data) as package:
        _main_part(package)  # a plain workbook, document or presentation, without macros
        names = package.namelist()
        replaced = {CORE_PART: _core(package, line), CUSTOM_PART: _custom(package, values)}
        if ROOT_RELS not in names:
            raise Refused("not_office")
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
                # Streamed, so a large sheet or picture never sits in memory whole.
                with package.open(info) as source, copy.open(entry, "w") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
            for part, _kind, _rel in added:
                copy.writestr(part, replaced[part])
    return out.getvalue()
