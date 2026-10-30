# step_io.py
#
# Writing the part's bodies to STEP, for customcst (which reads STEP with
# names and colors through XCAF: customcst/geometry/step_io.py read_step),
# and reading a STEP file's bodies for an Import (read_bodies: the same
# reading as customcst's, without its tessellation; kept per file content).
# Taken from customcst (write_step, kicad_step._write) with its lessons:
#   - each body is written as a compound: a solid written alone, if located,
#     becomes an instance of an unnamed part ('SOLID');
#   - OCC's writer leaves the solids' names empty, so the body's name is
#     filled into the file's MANIFOLD_SOLID_BREP('...' entities afterwards
#     (a body of several solids: each solid carries the body's name, and
#     customcst merges solids named like their part back into one body);
#   - names are text of ISO 10303-21: an apostrophe doubled, a backslash
#     doubled, a character beyond ASCII escaped (\X\F6 for ö, \X2\041F\X0\
#     for П): written raw (UTF-8) they read back mangled (VolumenkÃ¶rper16);
#     OCC's writer takes the names it writes (PRODUCT) as UTF-8 text but
#     writes them raw, so the whole file is escaped afterwards (a STEP file
#     is ASCII: anything beyond is a name);
#   - the document unit is millimeters, like the shapes;
#   - OCCT prints transfer statistics to stdout: silenced.
# The file is written next to its place and moved over it at the end, so a
# reader (customcst re-reading after a Save) never sees half a file; before
# that it is read back and its solids and volume compared.

import hashlib
import os
import re

from OCP.BRep import BRep_Builder
from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.XCAFPrs import XCAFPrs_DocumentExplorer, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes

from geometry.kernel import solid_count, volume

MM = 1e-3
SOLID_NAME = re.compile(r"MANIFOLD_SOLID_BREP\('[^']*'")
NON_ASCII = re.compile(r"[^\x00-\x7f]+")


def _quoted(name):
    r"""A name inside a STEP string's quotes (ISO 10303-21): ' and \ doubled; beyond ASCII: _escape, later."""
    return name.replace("\\", "\\\\").replace("'", "''")


def _escape(char):
    r"""One character beyond ASCII as ISO 10303-21 writes it: \X\HH (Latin-1), \X2\HHHH\X0\, \X4\...\X0\."""
    code = ord(char)
    if code <= 0xFF:
        return "\\X\\%02X" % code
    if code <= 0xFFFF:
        return "\\X2\\%04X\\X0\\" % code
    return "\\X4\\%08X\\X0\\" % code


def _silence_occt():
    """OCCT prints transfer statistics to stdout; keep only real failures."""
    try:
        from OCP.Message import Message, Message_Gravity

        for printer in Message.DefaultMessenger_s().Printers():
            printer.SetTraceLevel(Message_Gravity.Message_Fail)
    except Exception:   # cosmetic only
        pass


def _solids(shape):
    out, explorer = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        out.append(TopoDS.Solid(explorer.Current()))     # OCP 8: the downcasts are plain functions
        explorer.Next()
    return out


def export_part(path, part, result):
    """
    The bodies as the rebuild left them (model/rebuild.py), with their names
    and colors, to a STEP file (overwritten). Returns a note on what was left
    out (failed features), or "". ValueError if there is no body.
    """
    from model.part import color_rgb

    if not result.shapes:
        raise ValueError("the part has no body yet")
    colors = getattr(result, "colors", {})
    write_step(path, [(name, shape, colors.get(name) or (color_rgb(part.body_color(name))
                                                          if part.body_color(name) else None))
                      for name, shape in result.shapes.items()])
    failed = list(result.feature_errors)
    return f"without the failed {', '.join(failed)}" if failed else ""


def write_step(path, bodies):
    """
    bodies: [(name, OCP shape in mm, (r, g, b) in 0..1 or None)].
    Writes them as named, colored parts; IOError if it cannot, or if the
    file does not read back as written.
    """
    _silence_occt()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    XCAFDoc_DocumentTool.SetLengthUnit_s(doc, MM)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    names = []
    for name, shape, color in bodies:
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        solids = _solids(shape)
        for solid in solids:
            builder.Add(compound, solid)
        names += [name] * len(solids)
        label = shape_tool.AddShape(compound, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name, True))     # True: the str is UTF-8
        if color is not None:
            color_tool.SetColor(label, Quantity_Color(*map(float, color), Quantity_TOC_RGB),
                                XCAFDoc_ColorType.XCAFDoc_ColorGen)

    temp = f"{path}.writing.step"
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    if not writer.Transfer(doc, STEPControl_AsIs) or writer.Write(os.fspath(temp)) != IFSelect_RetDone:
        raise IOError(f"cannot write STEP file {path}")
    try:
        with open(temp, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if len(SOLID_NAME.findall(text)) != len(names):
            raise IOError("unexpected STEP output: the solid count differs")
        it = iter(names)
        text = SOLID_NAME.sub(lambda m: "MANIFOLD_SOLID_BREP('" + _quoted(next(it)) + "'", text)
        text = NON_ASCII.sub(lambda m: "".join(map(_escape, m.group(0))), text)
        with open(temp, "w", encoding="ascii") as f:
            f.write(text)
        _check_back(temp, bodies)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


# =====================================================
# Reading (an Import)
# =====================================================

_READ = {}                  # sha256 of a file's content -> its bodies (read once per content)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_bodies(path):
    """
    The bodies of a STEP file: [(name, kind, OCP shape in mm, placed, (r, g, b) or None)],
    in the file's order: each solid ("solid"), then each shell or face
    outside a solid ("sheet"); solids named like their own part are one
    body (a copper layer of pieces), names from the file (part names; in a
    multi-body part the bodies' own), made unique. ValueError if it does not
    read. Kept per content (a rebuild after every edit reads it once).
    """
    path = os.fspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    key = file_sha256(path)
    if key not in _READ:
        _READ[key] = _read(path)
    return _READ[key]


def _read(path):
    _silence_occt()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    XCAFDoc_DocumentTool.SetLengthUnit_s(doc, MM)   # the shapes in mm (OCP 8 cannot tell the file's unit)
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    if reader.ReadFile(path) != IFSelect_RetDone or not reader.Transfer(doc):
        raise ValueError(f"cannot read the STEP file {os.path.basename(path)}")
    transfer = reader.ChangeReader().WS().TransferReader()
    bodies, taken = [], set()
    for part_name, shape, location, color in _leaf_shapes(doc):
        parts = _split(shape)
        names = [_body_name(transfer, part, part_name, n, len(parts)) for n, (_, part) in enumerate(parts)]
        for name, kind, part in _merge_part_named(part_name, names, parts):
            bodies.append((_unique(name, taken), kind, part.Moved(location), color))
    if not bodies:
        raise ValueError(f"no solid or surface in {os.path.basename(path)}")
    return bodies


def _leaf_shapes(doc):
    """(part name, unlocated shape, location, color) of every leaf of the assembly tree (customcst's)."""
    explorer = XCAFPrs_DocumentExplorer(doc, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes)
    index = 0
    while explorer.More():
        node = explorer.Current()       # keep the node alive while using its fields
        index += 1
        name = _label_name(node.RefLabel) or f"body{index}"
        shape = XCAFDoc_ShapeTool.GetShape_s(node.RefLabel)
        color = None
        style = node.Style
        if style.IsSetColorSurf():
            c = style.GetColorSurf()
            color = (c.Red(), c.Green(), c.Blue())
        yield name, shape, node.Location, color
        explorer.Next()


def _label_name(label):
    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString().strip()
    return ""


def _split(shape):
    """[(kind, shape)]: each solid, then each shell or face outside a solid."""
    parts = []
    for kind, what, outside in (("solid", TopAbs_SOLID, None), ("sheet", TopAbs_SHELL, TopAbs_SOLID),
                                ("sheet", TopAbs_FACE, TopAbs_SHELL)):
        explorer = TopExp_Explorer(shape, what) if outside is None else TopExp_Explorer(shape, what, outside)
        while explorer.More():
            parts.append((kind, explorer.Current()))
            explorer.Next()
    return parts


def _body_name(transfer, part, part_name, index, count):
    """
    A single-body part is named after the part; in a multi-body part each
    body keeps its own name from the file (MANIFOLD_SOLID_BREP('port'...)),
    read through the STEP entity (walking XCAF sub-labels crashes OCP 8).
    """
    if count == 1:
        return part_name
    entity = transfer.EntityFromShapeResult(part, 1)
    name = entity.Name().ToCString().strip() if entity is not None and hasattr(entity, "Name") else ""
    return name or f"{part_name}#{index + 1}"


def _merge_part_named(part_name, names, parts):
    """[(name, kind, shape)]: the solids named like their part become one compound (as written by write_step)."""
    groups, order = {}, []
    for name, (kind, part) in zip(names, parts):
        key = (name, kind) if kind == "solid" and name == part_name else (name, kind, len(order))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(part)
    merged = []
    for key in order:
        shapes = groups[key]
        if len(shapes) == 1:
            merged.append((key[0], key[1], shapes[0]))
            continue
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for shape in shapes:
            builder.Add(compound, shape)
        merged.append((key[0], key[1], compound))
    return merged


def _unique(name, taken):
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{name}_{n}"
        n += 1
    taken.add(candidate)
    return candidate


def _check_back(path, bodies):
    """The file reads back with the same number of solids and the same volume."""
    reader = STEPControl_Reader()
    if reader.ReadFile(os.fspath(path)) != IFSelect_RetDone:
        raise IOError("the written STEP file does not read back")
    reader.TransferRoots()
    shape = reader.OneShape()
    want_solids = sum(solid_count(s) for _, s, _ in bodies)
    want_volume = sum(volume(s) for _, s, _ in bodies)
    if solid_count(shape) != want_solids or abs(volume(shape) - want_volume) > 1e-6 * max(want_volume, 1.0):
        raise IOError(f"the written STEP file reads back differently ({solid_count(shape)} solids, "
                      f"{volume(shape):.6g} mm³; written {want_solids}, {want_volume:.6g} mm³)")
