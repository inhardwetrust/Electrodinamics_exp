# step_io.py
#
# Writing the part's bodies to STEP, for customcst (which reads STEP with
# names and colors through XCAF: customcst/geometry/step_io.py read_step).
# Taken from customcst (write_step, kicad_step._write) with its lessons:
#   - each body is written as a compound: a solid written alone, if located,
#     becomes an instance of an unnamed part ('SOLID');
#   - OCC's writer leaves the solids' names empty, so the body's name is
#     filled into the file's MANIFOLD_SOLID_BREP('...' entities afterwards
#     (a body of several solids: each solid carries the body's name, and
#     customcst merges solids named like their part back into one body);
#   - the document unit is millimeters, like the shapes;
#   - OCCT prints transfer statistics to stdout: silenced.
# The file is written next to its place and moved over it at the end, so a
# reader (customcst re-reading after a Save) never sees half a file; before
# that it is read back and its solids and volume compared.

import os
import re

from OCP.BRep import BRep_Builder
from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

from geometry.kernel import solid_count, volume

MM = 1e-3
SOLID_NAME = re.compile(r"MANIFOLD_SOLID_BREP\('[^']*'")


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
    write_step(path, [(name, shape, color_rgb(part.body_color(name)) if part.body_color(name) else None)
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
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
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
        text = SOLID_NAME.sub(lambda m: "MANIFOLD_SOLID_BREP('" + next(it).replace("'", "") + "'", text)
        with open(temp, "w", encoding="utf-8") as f:
            f.write(text)
        _check_back(temp, bodies)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


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
