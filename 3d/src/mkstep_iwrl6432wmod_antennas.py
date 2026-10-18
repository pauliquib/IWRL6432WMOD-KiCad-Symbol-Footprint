# IWRL6432WMOD antenna overlay (STEP): the etched 60 GHz patch antennas that the TI model (SWRM078) leaves out.
# The overlay is a separate model, loaded by the footprint on top of IWRL6432WMOD_3D.step with the same
# offset / rotation, so it lives in TI's model coordinates (module PCB x 192.4..207.9, y 164.46..195.46, top z = 1.6).
# Geometry: TI SWRS350 (Dec 2025) §6.7, Fig. 6-4 (module photo, measured at 30.95 px/mm) and the array rules
# given there: RX1..RX3 at lambda/2 in azimuth, TX1 -> TX2 3*lambda/2 in azimuth and lambda/2 in elevation
# (lambda = 5.0 mm at 60 GHz). Inset-fed square patches rotated 45 deg, each in a copper-free window of the exposed
# top-layer ground, fed by grounded coplanar waveguides that run under the shield.
# Photo frame (u along the 31 mm side from the end opposite TX2, v across from the antenna edge) maps to the model
# as x = 192.4 + v, y = 164.46 + u.
# Usage: python3 3d/src/mkstep_iwrl6432wmod_antennas.py 3d/IWRL6432WMOD_antennas.step
# Needs OCP (pip install cadquery-ocp) and shapely.
import sys, math
from shapely.geometry import Polygon, Point, LineString, box as sbox
from shapely.ops import unary_union
from shapely.affinity import rotate, translate, affine_transform
from shapely.geometry.polygon import orient
from OCP.gp import gp_Pnt, gp_Vec
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.STEPCAFControl import STEPCAFControl_Writer, STEPCAFControl_Controller
from OCP.STEPControl import STEPControl_AsIs
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.Interface import Interface_Static
from OCP.IFSelect import IFSelect_RetDone

OUT = sys.argv[1]
NAME = 'IWRL6432WMOD_antennas'

# ------------------------------------------------------------------ dimensions (photo frame u, v in mm)
Z0 = 1.6                          # top of the TI module PCB
T_CU, T_DIEL = 0.035, 0.01        # copper above the bare laminate in the windows -> visibly etched
LAM2 = 2.5                        # lambda/2 at 60 GHz
PATCH = 1.28                      # patch side (rotated 45 deg)
INSET, W_FEED, G_INSET = 0.30, 0.13, 0.07   # inset feed: depth, line width, slot each side
BEND = 0.60                       # feed leaves the patch along the edge normal, turns towards +v at u = c + 0.60
W_CPW, G_CPW = 0.14, 0.08         # GCPW centre strip and gaps in the ground copper
SHIELD_U, SHIELD_V = 25.47, 4.49  # TI shield can footprint: u <= 25.47, v >= 4.49 (copper stops at the can wall)

RX_V, TX2_DV = 2.12, LAM2
RX = [(9.70 + i * LAM2, RX_V) for i in range(3)]
TX1 = (20.90, RX_V)
TX2 = (TX1[0] + 3 * LAM2, RX_V + TX2_DV)
WINDOWS = [(8.25, 0.82, 16.35, 3.42), (19.40, 0.80, 22.55, 3.40), (26.95, 3.28, 30.10, 5.88)]

C_CU = (0.76, 0.50, 0.36)          # bare copper as on the TI photo (sRGB, tuned for the KiCad 3D viewer)
C_LAM = (0.25, 0.26, 0.25)         # RF laminate seen through the etched windows / CPW gaps
C_PAD_RING = (0.45, 0.26, 0.20)    # darker copper ring of the round pad in the TX2 corner
C_PAD = (0.78, 0.53, 0.38)
C_MASK = (0.14, 0.22, 0.08)        # dark green solder mask over the TI model's light-green PCB (matches a KiCad board)
T_MASK = 0.01                      # mask thickness on top of the module PCB
SKIN = 0.01                        # mask skin around the module PCB edges (TI PCB: x 192.40..207.90, y 164.46..195.46, z 0..1.6)
MOD_U, MOD_V = 31.0, 15.5          # module PCB size in the photo frame (u along 31 mm, v along 15.5 mm)


def arc3(p0, p1, p2, n=40):
    """Points of the circular arc through p0, p1, p2."""
    (ax, ay), (bx, by), (cx, cy) = p0, p1, p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, a1, a2 = (math.atan2(p[1] - uy, p[0] - ux) for p in (p0, p1, p2))
    def norm(a): return (a - a0) % (2 * math.pi)
    sweep = norm(a2) if norm(a1) < norm(a2) else norm(a2) - 2 * math.pi
    return [(ux + r * math.cos(a0 + sweep * i / n), uy + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]


def strip(pts, w):
    return LineString(pts).buffer(w / 2, cap_style='flat', join_style='round', quad_segs=8)


def patch(c, v_end, tail=None):
    """Inset-fed patch with its microstrip feed; returns (copper, feed path from the patch edge to v_end)."""
    h = PATCH / 2
    sq = sbox(-h, -h, h, h)        # local frame: +x = outward normal of the fed edge
    sq = sq.difference(sbox(h - INSET, -(W_FEED / 2 + G_INSET), h + 0.01, W_FEED / 2 + G_INSET)).union(
        sbox(h - INSET, -W_FEED / 2, h, W_FEED / 2))
    sq = translate(rotate(sq, 45, origin=(0, 0)), *c)   # fed edge faces (+u, +v)
    k = BEND
    path = [(c[0] + (h - INSET) / math.sqrt(2), c[1] + (h - INSET) / math.sqrt(2)), (c[0] + k, c[1] + k),
            (c[0] + k, v_end)] + (tail or [])
    return sq, path


# ------------------------------------------------------------------ copper outline of the exposed antenna area
edge = arc3((29.80, 6.25), (27.90, 7.95), (SHIELD_U, 8.60))
area = Polygon([(7.94, 0.48), (29.45, 0.48), (30.45, 1.48), (30.45, 6.25)] + edge + [(SHIELD_U, SHIELD_V), (7.94, SHIELD_V)])
can = sbox(0, SHIELD_V, SHIELD_U, 15.5)
area = area.difference(can)

windows = unary_union([sbox(*w) for w in WINDOWS])
patches, feeds = [], []
for c in RX + [TX1]:
    p, path = patch(c, SHIELD_V + 0.2)
    patches.append(p); feeds.append(path)
tx2_tail = arc3((TX2[0] + BEND, 6.00), (27.60, 7.25), (SHIELD_U - 0.3, 8.15))
p, path = patch(TX2, 6.00, tx2_tail[1:])
patches.append(p); feeds.append(path)

feed_cu = unary_union([strip(f, W_FEED) for f in feeds])
gaps = unary_union([strip(f, W_CPW + 2 * G_CPW) for f in feeds]).difference(windows)   # CPW outside the windows
cpw_cu = unary_union([strip(f, W_CPW) for f in feeds]).difference(windows)
copper = area.difference(windows).difference(gaps)
copper = unary_union([copper, cpw_cu.intersection(area), feed_cu.intersection(windows),
                      unary_union(patches)]).intersection(area)
laminate = area.difference(copper)

pad_ring = Point(28.43, 12.83).buffer(1.55, quad_segs=24)
pad = Point(28.43, 12.83).buffer(0.53, quad_segs=16)
pad_ring_outer = pad_ring
pad_ring = pad_ring.difference(pad)

# solder mask on the rest of the module top: everything except the exposed antenna area, the shield can footprint and
# the round pad; the can covers its own footprint (narrow strips next to the can stay masked)
CAN_FP = sbox(0.57, 4.49, 25.47, 14.84)   # real can outline in the TI model (x 196.89..207.24, y 165.03..189.93)
mask = sbox(0, 0, MOD_U, MOD_V).difference(area).difference(CAN_FP).difference(pad_ring_outer)
# thin skin over the four PCB edges (the TI model's edges are light green as well)
skin = sbox(-SKIN, -SKIN, MOD_U + SKIN, MOD_V + SKIN).difference(sbox(0, 0, MOD_U, MOD_V))


# ------------------------------------------------------------------ shapely (u, v) -> OCC solids in model coordinates
def to_model(g):
    return affine_transform(g, [0, 1, 1, 0, 192.4, 164.46])   # x = 192.4 + v, y = 164.46 + u


def polys(g):
    g = to_model(g)
    return [orient(p) for p in (g.geoms if hasattr(g, 'geoms') else [g]) if p.area > 1e-6]


def wire(coords):
    mp = BRepBuilderAPI_MakePolygon()
    for x, y in list(coords)[:-1]: mp.Add(gp_Pnt(x, y, Z0))
    mp.Close()
    return mp.Wire()


def prism(p, t, z0=None):
    if z0 is not None:
        global Z0
        zs, Z0 = Z0, z0
        try:
            return prism(p, t)
        finally:
            Z0 = zs
    mf = BRepBuilderAPI_MakeFace(wire(p.exterior.coords), True)
    for r in p.interiors: mf.Add(wire(r.coords))
    return BRepPrimAPI_MakePrism(mf.Face(), gp_Vec(0, 0, t)).Shape()


def bbox(s):
    b = Bnd_Box(); BRepBndLib.Add_s(s, b); a, c = b.CornerMin(), b.CornerMax()
    return tuple(round(v, 3) for v in (a.X(), a.Y(), a.Z(), c.X(), c.Y(), c.Z()))


parts = ([(prism(p, T_CU), C_CU, 'Antenna copper') for p in polys(copper)] +
         [(prism(p, T_DIEL), C_LAM, 'Antenna laminate') for p in polys(laminate)] +
         [(prism(p, 0.02), C_PAD_RING, 'Pad ring') for p in polys(pad_ring)] +
         [(prism(p, T_CU), C_PAD, 'Pad') for p in polys(pad)] +
         [(prism(p, T_MASK), C_MASK, 'Solder mask') for p in polys(mask)] +
         [(prism(p, Z0 + T_MASK, 0.0), C_MASK, 'Solder mask edge') for p in polys(skin)])

# ------------------------------------------------------------------ write STEP with colours
doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main()); ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
comp = TopoDS_Compound(); bld = BRep_Builder(); bld.MakeCompound(comp)
for s, _, _ in parts: bld.Add(comp, s)
root = st.AddShape(comp, False)
TDataStd_Name.Set_s(root, TCollection_ExtendedString(NAME))
for s, c, nm in parts:
    lab = st.AddSubShape(root, s)
    ct.SetColor(lab, Quantity_Color(c[0], c[1], c[2], Quantity_TypeOfColor.Quantity_TOC_sRGB), XCAFDoc_ColorType.XCAFDoc_ColorSurf)
    TDataStd_Name.Set_s(lab, TCollection_ExtendedString(nm))
STEPCAFControl_Controller.Init_s()        # must run before setting the statics, otherwise they are reset
Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
Interface_Static.SetCVal_s("write.step.unit", "MM")
Interface_Static.SetIVal_s("write.surfacecurve.mode", 0)
Interface_Static.SetCVal_s("write.step.product.name", NAME)
w = STEPCAFControl_Writer(); w.SetColorMode(True); w.SetNameMode(True)
w.Transfer(doc, STEPControl_AsIs)
assert w.Write(OUT) == IFSelect_RetDone
print('solids', len(parts), 'bbox', bbox(comp))
print('patch centres (model x, y):', [(round(192.4 + v, 2), round(164.46 + u, 2)) for u, v in RX + [TX1, TX2]])
