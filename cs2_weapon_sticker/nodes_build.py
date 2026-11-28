"""
Node-graph translation of the CS2 "csgo_weapon_sticker" (Weapon Sticker Preview) pixel shader.

Every block below mirrors a block of the decompiled GLSL. The original SPIR-V temporaries are
quoted in comments (e.g. `_10176`) so the graph can be checked against the source line by line.

Conventions
-----------
* Source 2 UVs have V pointing down, Blender UVs have V pointing up. All UV math that the shader
  does with raw values (spectrum lookups, glitter tiling, the "uv - 0.5" normal trick, the edge
  offset) is done in Source convention (`*_src`) and converted back right before each sample.
* The shader builds its world normal as  T * w.x + B_src * w.y + N * w.z  where B_src = cross(N,T)*sign.
  Because of the V flip, Blender's bitangent is -B_src, so a Source "working space" vector w is fed to
  Blender's Normal Map node as (w.x, -w.y, w.z).
* Static/dynamic branches on uniforms become mix(a, b, flag) with 0/1 flags (exact for 0/1).
"""

import math
import bpy

VERSION = 15

LUM = (0.2125000059604645, 0.7153999805450439, 0.07209999859333038)
HEMI_BIAS = 1.00392162799835205078125  # 256/255


def _levels(v):
    # Expression(float3(-v.x, -1.4427*log(max(.0001, 1-v.y)), 2-v.z))
    return (-v[0], -1.4427 * math.log(max(0.0001, 1.0 - v[1])), 2.0 - v[2])


ALBEDO_LEVELS = _levels((-.045, .4, 1.15))
DARK_METALLIC_LEVELS = _levels((-.1, .4, 1.08))
HOLO_LEVELS = _levels((-.05, .35, 1.15))
METALLIC_LEVELS = _levels((-.45, .4, 1.08))
COLOR_BOOST_FACTOR = 64.0 - 1.0  # g_fColorBoostFactor = Expression(64-1)

FIT_NAME = "CS2 Sticker - PBR Color Fit"
SPARKLE_NAME = "CS2 Sticker - Glitter Sparkle"
DECODE_NAME = "CS2 Sticker - Normal Decode"
MAIN_PREFIX = "CS2 Weapon Sticker"
SUN_NODE = "SunDirection"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _sock(coll, ident):
    for s in coll:
        if s.identifier == ident:
            return s
    raise KeyError(ident)


class Builder:
    """Tiny DSL that turns expressions into shader nodes (with constant folding)."""

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links
        self._sep_cache = {}

    # ------------------------------------------------------------------ plumbing
    def node(self, idname, hide=True, label=None, name=None, **props):
        n = self.nodes.new(idname)
        for k, v in props.items():
            setattr(n, k, v)
        n.hide = hide
        if label:
            n.label = label
        if name:
            n.name = name
        return n

    def feed(self, sock, value):
        if value is None:
            return
        if isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, sock)
            return
        if sock.type in {'VECTOR', 'RGBA'}:
            n = len(sock.default_value)
            if _num(value):
                value = (float(value),) * 3 + ((1.0,) if n == 4 else ())
            else:
                value = tuple(float(x) for x in value)
                if len(value) < n:
                    value = value + (1.0,) * (n - len(value))
                value = value[:n]
        sock.default_value = value

    def call(self, tree, label=None, **inputs):
        n = self.node('ShaderNodeGroup', label=label)
        n.node_tree = tree
        for k, v in inputs.items():
            self.feed(n.inputs[k.replace("_", " ")], v)
        return n

    # ------------------------------------------------------------------ scalar math
    def math(self, op, a, b=None, c=None, clamp=False):
        n = self.node('ShaderNodeMath', operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        return n.outputs[0]

    def add(self, a, b):
        return a + b if _num(a) and _num(b) else self.math('ADD', a, b)

    def sub(self, a, b):
        return a - b if _num(a) and _num(b) else self.math('SUBTRACT', a, b)

    def mul(self, a, b):
        return a * b if _num(a) and _num(b) else self.math('MULTIPLY', a, b)

    def div(self, a, b):
        return a / b if _num(a) and _num(b) else self.math('DIVIDE', a, b)

    def pow(self, a, b):
        return self.math('POWER', a, b)

    def min(self, a, b):
        return self.math('MINIMUM', a, b)

    def max(self, a, b):
        return self.math('MAXIMUM', a, b)

    def abs(self, a):
        return self.math('ABSOLUTE', a)

    def fract(self, a):
        return self.math('FRACT', a)

    def sat(self, a):
        return self.math('ADD', a, 0.0, clamp=True)

    def gt(self, a, b):
        return self.math('GREATER_THAN', a, b)

    def lt(self, a, b):
        return self.math('LESS_THAN', a, b)

    def step(self, edge, x):
        # GLSL step(edge, x) = x < edge ? 0 : 1
        return self.sub(1.0, self.lt(x, edge))

    def mixf(self, a, b, t):
        if _num(a) and _num(b) and _num(t):
            return a + (b - a) * t
        n = self.node('ShaderNodeMix', data_type='FLOAT', clamp_factor=False)
        self.feed(_sock(n.inputs, 'Factor_Float'), t)
        self.feed(_sock(n.inputs, 'A_Float'), a)
        self.feed(_sock(n.inputs, 'B_Float'), b)
        return _sock(n.outputs, 'Result_Float')

    def smoothstep(self, e0, e1, x):
        # GLSL smoothstep (also valid for e0 > e1)
        t = self.sat(self.div(self.sub(x, e0), self.sub(e1, e0)))
        return self.mul(self.mul(t, t), self.sub(3.0, self.mul(2.0, t)))

    # ------------------------------------------------------------------ vector math
    def vmath(self, op, a, b=None, c=None, scale=None, out=0):
        n = self.node('ShaderNodeVectorMath', operation=op)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        if scale is not None:
            self.feed(n.inputs[3], scale)
        return n.outputs[out]

    def vadd(self, a, b): return self.vmath('ADD', a, b)
    def vsub(self, a, b): return self.vmath('SUBTRACT', a, b)
    def vmul(self, a, b): return self.vmath('MULTIPLY', a, b)
    def vscale(self, a, s): return self.vmath('SCALE', a, scale=s)
    def vdot(self, a, b): return self.vmath('DOT_PRODUCT', a, b, out=1)
    def vlen(self, a): return self.vmath('LENGTH', a, out=1)
    def vnorm(self, a): return self.vmath('NORMALIZE', a)
    def vreflect(self, a, n): return self.vmath('REFLECT', a, n)
    def vmin(self, a, b): return self.vmath('MINIMUM', a, b)
    def vmax(self, a, b): return self.vmath('MAXIMUM', a, b)
    def vsin(self, a): return self.vmath('SINE', a)
    def vsat(self, a): return self.vmin(self.vmax(a, 0.0), 1.0)
    def lum(self, a): return self.vdot(a, LUM)
    def flip_y(self, a): return self.vmul(a, (1.0, -1.0, 1.0))

    def vmix(self, a, b, t):
        n = self.node('ShaderNodeMix', data_type='VECTOR', clamp_factor=False)
        n.factor_mode = 'UNIFORM'
        self.feed(_sock(n.inputs, 'Factor_Float'), t)
        self.feed(_sock(n.inputs, 'A_Vector'), a)
        self.feed(_sock(n.inputs, 'B_Vector'), b)
        return _sock(n.outputs, 'Result_Vector')

    def sep(self, v):
        key = v.as_pointer()
        if key not in self._sep_cache:
            n = self.node('ShaderNodeSeparateXYZ')
            self.feed(n.inputs[0], v)
            self._sep_cache[key] = (n.outputs[0], n.outputs[1], n.outputs[2])
        return self._sep_cache[key]

    def comb(self, x, y, z=0.0):
        n = self.node('ShaderNodeCombineXYZ')
        self.feed(n.inputs[0], x)
        self.feed(n.inputs[1], y)
        self.feed(n.inputs[2], z)
        return n.outputs[0]

    def max3(self, v):
        x, y, z = self.sep(v)
        return self.max(x, self.max(y, z))

    # ------------------------------------------------------------------ shading helpers
    def image(self, name, label, vector, interpolation='Linear', extension='REPEAT'):
        n = self.node('ShaderNodeTexImage', hide=False, label=label, name=name,
                      interpolation=interpolation, extension=extension)
        self.feed(n.inputs['Vector'], vector)
        return n.outputs['Color'], n.outputs['Alpha']

    def normal_map(self, tangent_vec):
        """Blender tangent-space vector (-1..1) -> world normal, via the Normal Map node."""
        col = self.vadd(self.vscale(tangent_vec, 0.5), 0.5)
        n = self.node('ShaderNodeNormalMap', space='TANGENT')
        self.feed(n.inputs['Strength'], 1.0)
        self.feed(n.inputs['Color'], col)
        return n.outputs['Normal']


# ====================================================================== group helpers

def _fresh_group(name):
    g = bpy.data.node_groups.get(name)
    if g is None:
        g = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    else:
        g.nodes.clear()
        g.interface.clear()
    return g


def _iface(g, name, in_out, stype, default=None, lo=None, hi=None, parent=None,
           hide_value=False, desc="", factor=False):
    kw = dict(in_out=in_out, socket_type=stype, description=desc)
    if parent is not None:
        kw["parent"] = parent
    s = g.interface.new_socket(name, **kw)
    if default is not None and hasattr(s, "default_value"):
        s.default_value = default
    if lo is not None and hasattr(s, "min_value"):
        s.min_value = lo
    if hi is not None and hasattr(s, "max_value"):
        s.max_value = hi
    if factor and hasattr(s, "subtype"):
        try:
            s.subtype = 'FACTOR'
        except TypeError:
            pass
    if hide_value:
        s.hide_value = True
    return s


def auto_layout(tree, dx=230):
    """Left-to-right layering by longest path with a barycenter ordering pass."""
    nodes = list(tree.nodes)
    inc = {n.name: [] for n in nodes}
    for l in tree.links:
        inc[l.to_node.name].append(l.from_node.name)
    depth = {}

    def d(name):
        if name in depth:
            return depth[name]
        depth[name] = 0  # cycle guard
        srcs = inc[name]
        depth[name] = (max(d(s) for s in srcs) + 1) if srcs else 0
        return depth[name]

    for n in nodes:
        d(n.name)
    cols = {}
    for n in nodes:
        cols.setdefault(depth[n.name], []).append(n)
    ypos = {}
    for c in sorted(cols):
        col = cols[c]

        def key(n):
            ys = [ypos[s] for s in inc[n.name] if s in ypos]
            return -(sum(ys) / len(ys)) if ys else 0.0

        col.sort(key=key)
        y = 0.0
        for n in col:
            n.location = (c * dx, y)
            ypos[n.name] = y
            if n.hide:
                h = 34
            elif n.bl_idname == 'ShaderNodeTexImage':
                h = 290
            elif n.bl_idname in {'NodeGroupInput', 'NodeGroupOutput'}:
                h = 60 + 22 * len(n.outputs if n.bl_idname == 'NodeGroupInput' else n.inputs)
            else:
                h = 220
            y -= h + 10


def _up_to_date(name):
    g = bpy.data.node_groups.get(name)
    return g is not None and g.get("cs2_version") == VERSION and len(g.nodes) > 0


# ====================================================================== shared groups

def get_decode_group():
    """Normal texture decode. Hemi-octahedral RG (raw Source 2) or standard RGB (OpenGL / DirectX)."""
    if _up_to_date(DECODE_NAME):
        return bpy.data.node_groups[DECODE_NAME]
    g = _fresh_group(DECODE_NAME)
    _iface(g, "Normal", 'OUTPUT', 'NodeSocketVector')
    _iface(g, "Color", 'INPUT', 'NodeSocketColor', (0.502, 0.502, 1.0, 1.0))
    _iface(g, "Standard", 'INPUT', 'NodeSocketFloat', 0.0, 0.0, 1.0, factor=True)
    _iface(g, "DirectX", 'INPUT', 'NodeSocketFloat', 0.0, 0.0, 1.0, factor=True)
    b = Builder(g)
    gi = b.node('NodeGroupInput', hide=False)
    go = b.node('NodeGroupOutput', hide=False)
    r, gg, bb = b.sep(gi.outputs["Color"])
    # x = (r+g) - 1.0039216 ; y = r - g ; z = 1 - |x| - |y|
    x = b.sub(b.add(r, gg), HEMI_BIAS)
    y = b.sub(r, gg)
    z = b.sub(b.sub(1.0, b.abs(x)), b.abs(y))
    hemi = b.vnorm(b.comb(x, y, z))
    sy = b.mul(b.sub(b.mul(gg, 2.0), 1.0), b.sub(1.0, b.mul(2.0, gi.outputs["DirectX"])))
    std = b.vnorm(b.comb(b.sub(b.mul(r, 2.0), 1.0), sy, b.sub(b.mul(bb, 2.0), 1.0)))
    b.feed(go.inputs["Normal"], b.vmix(hemi, std, gi.outputs["Standard"]))
    auto_layout(g)
    g["cs2_version"] = VERSION
    return g


def get_sparkle_group():
    """Glitter sparkle response of one glitter layer (_17144 / _17145)."""
    if _up_to_date(SPARKLE_NAME):
        return bpy.data.node_groups[SPARKLE_NAME]
    g = _fresh_group(SPARKLE_NAME)
    _iface(g, "Sparkle", 'OUTPUT', 'NodeSocketVector')
    _iface(g, "Sine", 'INPUT', 'NodeSocketVector', hide_value=True)
    b = Builder(g)
    gi = b.node('NodeGroupInput', hide=False)
    go = b.node('NodeGroupOutput', hide=False)
    s = gi.outputs["Sine"]
    t1 = b.pow(b.lum(b.vsat(b.vscale(s, -1.0))), 4.0)
    t2 = b.mul(b.lum(b.vsat(b.vsub(0.14999997615814209, s))), 0.25)
    q = b.vscale(b.vmax(b.vsub(s, 0.9900000095367432), 0.0), 100.00009918212891)
    qx, qy, qz = b.sep(q)
    q4 = b.comb(b.pow(qy, 4.0), b.pow(qz, 4.0), b.pow(qx, 4.0))  # pow(q.yzx, 4)
    t3 = b.vscale(b.vadd(q, q4), 3.0)  # * 4.0 * 0.75
    b.feed(go.inputs["Sparkle"], b.vadd(b.add(t1, t2), t3))
    auto_layout(g)
    g["cs2_version"] = VERSION
    return g


def get_fit_group():
    """
    Automatic PBR color fitting, shared by the glitter (_9756), sticker (_22936/_22937)
    and holographic (_25191) paths.
        n  = normalize(max(0.0003, NIn)) * 1.06
        A  = max(n*LX*1.732/length(n)/lum(n), n*mix(Lv.x, Lv.z, sat(pow(max3(CMax)*Boost, Lv.y))))
        B  = mix(A, min(Lv.z, A + L*AddScale), max(0, Boost-1) * BoostFactorInv)
        out= mix(LX, B, pow(smoothstep(0.0003, LX, L), 0.5))
    """
    if _up_to_date(FIT_NAME):
        return bpy.data.node_groups[FIT_NAME]
    g = _fresh_group(FIT_NAME)
    _iface(g, "Color", 'OUTPUT', 'NodeSocketVector')
    _iface(g, "N In", 'INPUT', 'NodeSocketVector', hide_value=True)
    _iface(g, "C Max", 'INPUT', 'NodeSocketVector', hide_value=True)
    _iface(g, "Luminance", 'INPUT', 'NodeSocketFloat', 0.0)
    _iface(g, "Levels", 'INPUT', 'NodeSocketVector', ALBEDO_LEVELS)
    _iface(g, "Level X", 'INPUT', 'NodeSocketFloat', ALBEDO_LEVELS[0])
    _iface(g, "Boost", 'INPUT', 'NodeSocketFloat', 1.0)
    _iface(g, "Boost Factor Inv", 'INPUT', 'NodeSocketFloat', 1.0 / COLOR_BOOST_FACTOR)
    _iface(g, "Add Scale", 'INPUT', 'NodeSocketFloat', 1.0)
    b = Builder(g)
    gi = b.node('NodeGroupInput', hide=False)
    go = b.node('NodeGroupOutput', hide=False)
    I = gi.outputs
    n = b.vscale(b.vnorm(b.vmax(I["N In"], 0.0003000000142492354)), 1.059999942779541)
    t1 = b.vscale(n, b.div(b.mul(I["Level X"], 1.7319999933242798), b.mul(b.vlen(n), b.lum(n))))
    lx, ly, lz = b.sep(I["Levels"])
    pw = b.sat(b.pow(b.mul(b.max3(I["C Max"]), I["Boost"]), ly))
    t2 = b.vscale(n, b.mixf(lx, lz, pw))
    A = b.vmax(t1, t2)
    boost_t = b.mul(b.max(b.sub(I["Boost"], 1.0), 0.0), I["Boost Factor Inv"])
    Bv = b.vmix(A, b.vmin(lz, b.vadd(A, b.mul(I["Luminance"], I["Add Scale"]))), boost_t)
    sm = b.pow(b.smoothstep(0.0003000000142492354, I["Level X"], I["Luminance"]), 0.5)
    b.feed(go.inputs["Color"], b.vmix(I["Level X"], Bv, sm))
    auto_layout(g)
    g["cs2_version"] = VERSION
    return g


# ====================================================================== main group

# (panel, name, socket type, default, min, max, description)
INPUTS = [
    (None, "UV", 'NodeSocketVector', None, None, None, "UV used for the weapon / backing textures"),
    (None, "Sticker UV", 'NodeSocketVector', None, None, None, "UV used for all sticker textures"),
    ("Sticker Color", "Enable Sticker", 'NodeSocketFloat', 1.0, 0.0, 1.0, "g_bEnableSticker0"),
    ("Sticker Color", "Sticker Tint", 'NodeSocketColor', (1.0, 1.0, 1.0, 1.0), None, None, "g_vColorTintSticker0"),
    ("Sticker Color", "Tint Saturate", 'NodeSocketFloat', 1.0, 0.0, 1.0, "g_flTintSaturateSticker0 (UI value)"),
    ("Sticker Color", "Automatic PBR Color Fitting", 'NodeSocketFloat', 1.0, 0.0, 1.0, "g_bAutomaticPBRColorFittingSticker0"),
    ("Sticker Color", "Legacy Tint Multiply", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bLegacyTintMultiplySticker0"),
    ("Sticker Color", "Color Boost", 'NodeSocketFloat', 1.0, 0.0, 64.0, "g_flColorBoostSticker0"),
    ("Sticker Color", "Metallic", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bMetallicSticker0"),
    ("SFX", "Holographic", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bHolographicSticker0"),
    ("SFX", "Clamp Spectrum V", 'NodeSocketFloat', 1.0, 0.0, 1.0, "g_bClampSpectrumVSticker0"),
    ("SFX", "SFX Color Boost", 'NodeSocketFloat', 1.0, 0.0, 64.0, "g_flSfxColorBoostSticker0"),
    ("SFX", "Foil Using Glitter Mask", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bFoilUsingGlitterMaskSticker0"),
    ("SFX", "Glitter", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bGlitterSticker0"),
    ("SFX", "Glitter Scale", 'NodeSocketFloat', 1.0, 0.0, 2.0, "g_flGlitterScaleSticker0"),
    ("SFX", "Self Illum", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bSelfIllumSticker0"),
    ("Wear", "Wear", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_flSticker0Wear"),
    ("Wear", "Wear Bias X", 'NodeSocketFloat', 1.0, 0.001, 4.0, "g_vWearBiasSticker0.x"),
    ("Wear", "Wear Bias Y", 'NodeSocketFloat', 1.0, -4.0, 4.0, "g_vWearBiasSticker0.y"),
    ("Wear", "Wear Scratches", 'NodeSocketFloat', 1.0, 0.0, 1.0, "g_fWearScratchesSticker0"),
    ("Wear", "Paper Backing", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bPaperBackingSticker0"),
    ("Wear", "Preserve Roughness", 'NodeSocketFloat', 0.0, 0.0, 1.0, "g_bPreserveRoughnessSticker0"),
    ("Base Weapon", "Base Normal", 'NodeSocketColor', (0.5, 0.5, 1.0, 1.0), None, None,
     "Normal map of the weapon under the sticker (g_tNormal). Connect an Image Texture set to Non-Color, "
     "sampled with the weapon's UV. Left empty, the surface is flat"),
    ("Base Weapon", "Base Normal Hemi-Oct", 'NodeSocketFloat', 0.0, 0.0, 1.0,
     "1 = raw compiled Source 2 normal (hemi-octahedral RG), 0 = regular RGB normal map"),
    ("Base Weapon", "Base Normal DirectX", 'NodeSocketFloat', 0.0, 0.0, 1.0,
     "Flip green for DirectX-style RGB normal maps"),
    ("Base Weapon", "Base Normal Strength", 'NodeSocketFloat', 0.1, 0.0, 1.0,
     "How strongly the weapon's normal map shows through the sticker. 1 = full strength, 0 = flat"),
    ("Base Weapon", "Base Shader", 'NodeSocketShader', None, None, None,
     "The weapon's own shader. With Layer Over Base Shader on, the sticker is drawn over it instead of over transparency"),
    ("Base Weapon", "Layer Over Base Shader", 'NodeSocketFloat', 0.0, 0.0, 1.0,
     "On: show Base Shader outside the sticker (stickers inside the weapon material). Off: transparent (sticker planes)"),
    ("Base Weapon", "Weapon Sticker Mask", 'NodeSocketFloat', 1.0, 0.0, 1.0,
     "Where stickers may appear on the weapon: red channel of the weapon's *_sticker_mask_hd (or *_sticker_mask_legacy) texture. 1 = everywhere"),
    ("Sticker Placement", "UV Placement", 'NodeSocketFloat', 0.0, 0.0, 1.0,
     "Place the sticker on the weapon's UV map like CS2 models do (connect the weapon's second UV map to "
     "Sticker UV). Off: Sticker UV is used as is, for sticker planes and legacy sticker meshes"),
    ("Sticker Placement", "Slot X", 'NodeSocketFloat', 0.0, -1.0, 1.0,
     "Horizontal offset of the sticker's center from the middle of the UV map"),
    ("Sticker Placement", "Slot Y", 'NodeSocketFloat', 0.0, -1.0, 1.0,
     "Vertical offset of the sticker's center from the middle of the UV map (negative = towards the top, as in CS2)"),
    ("Sticker Placement", "Slot Scale", 'NodeSocketFloat', 1.0, 0.01, 200.0,
     "How many times smaller than the whole UV map the sticker is"),
    ("Sticker Placement", "Rotation", 'NodeSocketFloat', 0.0, -360.0, 360.0,
     "Extra rotation of the sticker in degrees, counter-clockwise"),
    ("Advanced", "Standard Normal Maps", 'NodeSocketFloat', 0.0, 0.0, 1.0, "0 = raw Source 2 hemi-octahedral RG, 1 = regular RGB normal maps"),
    ("Advanced", "DirectX Normals", 'NodeSocketFloat', 0.0, 0.0, 1.0, "Flip green for standard DirectX-style normal maps"),
    ("Advanced", "Invert Glitter Mask", 'NodeSocketFloat', 0.0, 0.0, 1.0, "Glitter mask is stored inverted (compiled g_tSfxMaskSticker0 alpha: black = glitter)"),
    ("Advanced", "Wear Mask From Sticker Alpha", 'NodeSocketFloat', 1.0, 0.0, 1.0, "Read the wear mask from the sticker color's alpha (set automatically when no wear mask texture is given)"),
]

FLAG_INPUTS = {
    "Enable Sticker", "Automatic PBR Color Fitting", "Legacy Tint Multiply", "Metallic",
    "Holographic", "Clamp Spectrum V", "Foil Using Glitter Mask", "Glitter", "Self Illum", "Paper Backing",
    "Preserve Roughness", "Standard Normal Maps", "DirectX Normals",
    "Invert Glitter Mask", "Wear Mask From Sticker Alpha", "Base Normal Hemi-Oct", "Base Normal DirectX", "UV Placement", "Layer Over Base Shader",
}

OUTPUTS = [
    ("BSDF", 'NodeSocketShader'),
    ("Base Color", 'NodeSocketColor'),
    ("Metallic", 'NodeSocketFloat'),
    ("Roughness", 'NodeSocketFloat'),
    ("Normal", 'NodeSocketVector'),
    ("Emission", 'NodeSocketColor'),
    ("Glitter", 'NodeSocketColor'),
    ("Sticker Mask", 'NodeSocketFloat'),
]

def _build_interface(g):
    for name, stype in OUTPUTS:
        _iface(g, name, 'OUTPUT', stype)
    panels = {}
    for panel, name, stype, default, lo, hi, desc in INPUTS:
        if panel is None:
            _iface(g, name, 'INPUT', stype, default, lo, hi, hide_value=(stype == 'NodeSocketVector'), desc=desc)
    for panel, name, stype, default, lo, hi, desc in INPUTS:
        if panel is None:
            continue
        if panel not in panels:
            panels[panel] = g.interface.new_panel(panel, default_closed=(panel == "Advanced"))
        _iface(g, name, 'INPUT', stype, default, lo, hi, parent=panels[panel], desc=desc,
               factor=(name in FLAG_INPUTS))


def build_main_group(g):
    """(Re)build the per-material sticker group in place."""
    g.nodes.clear()
    g.interface.clear()
    _build_interface(g)

    FIT = get_fit_group()
    SPARKLE = get_sparkle_group()
    DECODE = get_decode_group()

    b = Builder(g)
    gi = b.node('NodeGroupInput', hide=False)
    go = b.node('NodeGroupOutput', hide=False)

    def I(name):
        return gi.outputs[name]

    def fit(n_in, c_max, l, levels, lx, boost, add_scale):
        return b.call(FIT, N_In=n_in, C_Max=c_max, Luminance=l, Levels=levels, Level_X=lx,
                      Boost=boost, Boost_Factor_Inv=1.0 / COLOR_BOOST_FACTOR,
                      Add_Scale=add_scale).outputs["Color"]

    def decode(color, std, dx):
        return b.call(DECODE, Color=color, Standard=std, DirectX=dx).outputs["Normal"]

    def sparkle(sine):
        return b.call(SPARKLE, Sine=sine).outputs["Sparkle"]

    def to_bl(v_src):
        x, y, _ = b.sep(v_src)
        return b.comb(x, b.sub(1.0, y), 0.0)

    geo = b.node('ShaderNodeNewGeometry')
    V = geo.outputs['Incoming']        # normalize(cameraPos - worldPos)
    Ngeo = geo.outputs['Normal']       # _24347
    std = I("Standard Normal Maps")
    dx = I("DirectX Normals")
    uv = I("UV")

    def rough_of(color):
        return b.sep(color)[0]

    # ------------------------------------------------------------------ base layer
    # The weapon normal map (g_tNormal) comes in through the "Base Normal" input, so it can be
    # sampled with the weapon's own UV in the material. Its format is set separately from the sticker's.
    Nc = I("Base Normal")
    std_base = b.sub(1.0, I("Base Normal Hemi-Oct"))
    dx_base = I("Base Normal DirectX")
    Rc, _ = b.image("IMG_Rough", "Base Roughness (g_tNormal B)", uv)
    # base color / metalness / tint are fixed to the shader defaults; AO is not used
    M = 0.0                                                   # _18169 (g_tMetalness default)
    rough_base = rough_of(Rc)                                  # _12835
    # Base Normal Strength fades the weapon's bumps under the sticker towards flat
    n_base_raw = b.vnorm(b.vmix((0.0, 0.0, 1.0), decode(Nc, std_base, dx_base), I("Base Normal Strength")))  # _18354
    w_base = b.flip_y(n_base_raw)                              # _20480
    albedo0 = (1.0, 1.0, 1.0)                                  # _23987 (g_tColor default * white tint)
    N_base_world = b.normal_map(n_base_raw)                    # _15878

    # ------------------------------------------------------------------ sticker placement
    # CS2 models (no separate sticker mesh) place the sticker on the weapon's second UV map:
    #   sticker_uv = R(-rotation) * (uv - center) * slot_scale + 0.5,  center = (0.5 + X, 0.5 - Y)
    # (Y is stored like in CS2: negative = towards the top of the UV map). Off: Sticker UV as is.
    rx, ry, _ = b.sep(I("Sticker UV"))
    place_on = I("UV Placement")
    du = b.sub(rx, b.add(0.5, I("Slot X")))
    dv = b.sub(ry, b.sub(0.5, I("Slot Y")))
    rad = b.mul(I("Rotation"), math.pi / 180.0)
    cs, sn = b.math('COSINE', rad), b.math('SINE', rad)
    scale = I("Slot Scale")
    pu = b.add(b.mul(b.add(b.mul(du, cs), b.mul(dv, sn)), scale), 0.5)
    pv = b.add(b.mul(b.sub(b.mul(dv, cs), b.mul(du, sn)), scale), 0.5)
    sx = b.mixf(rx, pu, place_on)
    sy = b.mixf(ry, pv, place_on)

    # ------------------------------------------------------------------ sticker coordinates
    u_src = sx
    v_src = b.sub(1.0, sy)
    uc = b.sat(u_src)
    vc = b.sat(v_src)
    uvC = b.comb(uc, vc, 0.0)                                  # _19287
    uvC_bl = to_bl(uvC)
    # _12887: sticker UV outside 0..1
    out_of_range = b.gt(b.add(b.abs(b.sub(u_src, uc)), b.abs(b.sub(v_src, vc))), 0.0)

    S, _ = b.image("IMG_Sticker", "Sticker Color (g_tSticker0 RGB)", uvC_bl, extension='EXTEND')
    # wear mask = g_tSticker0.a. The wear-mask nodes hold either the wear mask texture (read R)
    # or, when none is given, the sticker color image (read its alpha).
    wm_from_alpha = I("Wear Mask From Sticker Alpha")

    def wear_mask(col, alpha):
        return b.mixf(b.sep(col)[0], alpha, wm_from_alpha)

    Sa = wear_mask(*b.image("IMG_WearMask", "Wear Mask (g_tSticker0 A)", uvC_bl, extension='EXTEND'))  # _7426
    a = b.sat(b.mul(Sa, 12.75))                                # _10511 / _24305

    # edge / thickness sample offset by the view-space normal (_17335.w)
    vt = b.node('ShaderNodeVectorTransform', vector_type='NORMAL', convert_from='WORLD', convert_to='CAMERA')
    b.feed(vt.inputs[0], N_base_world)
    ncx, _, ncz = b.sep(vt.outputs[0])
    # off = (dot(cross(up, dir), -N), dot(dir, N)) * 0.01  ->  (N_cam.x, -N_cam.z) * 0.01
    off = b.comb(b.mul(ncx, 0.009999999776482582), b.mul(ncz, -0.009999999776482582), 0.0)
    edge_src = b.vsat(b.vsub(uvC, off))
    Sa_edge = wear_mask(*b.image("IMG_WearMaskEdge", "Wear Mask, edge sample (g_tSticker0 A)", to_bl(edge_src), extension='EXTEND'))
    a2 = b.max(a, b.mul(b.sat(b.mul(Sa_edge, 12.75)), 0.699999988079071))   # _23168.w

    skip = b.max(out_of_range, b.sub(1.0, b.gt(a2, 0.0)))      # _22788 || _12887
    apply = b.mul(b.sub(1.0, skip), I("Enable Sticker"))

    NRc, _ = b.image("IMG_StickerNR", "Sticker Normal (g_tNormalRoughnessSticker0 RG)", uvC_bl, extension='EXTEND')
    SRc, _ = b.image("IMG_StickerRough", "Sticker Roughness (g_tNormalRoughnessSticker0 B)", uvC_bl, extension='EXTEND')
    SIc, _ = b.image("IMG_SelfIllum", "Self Illum Mask (g_tNormalRoughnessSticker0 A)", uvC_bl, extension='EXTEND')
    rough_s = rough_of(SRc)                                    # _13757
    self_mask = b.sep(SIc)[0]                                  # _19373.w
    ns = b.flip_y(decode(NRc, std, dx))                        # _15728
    n10176 = b.vnorm(b.vadd(w_base, b.vscale(ns, b.mul(a2, 2.0))))
    metal_flag = I("Metallic")                                 # _22924
    auto_f = I("Automatic PBR Color Fitting")

    # ------------------------------------------------------------------ glitter
    # g_tSfxMaskSticker0: RGB = holo mask, A = inverted glitter mask. Here they are separate textures;
    # the glitter mask is read as "white = glitter" (1 - A), unless "Invert Glitter Mask" is on.
    inv_gm = I("Invert Glitter Mask")

    def glitter_amount(col):
        # empty slot = black default: Invert off -> no glitter, Invert on -> glitter everywhere
        r = b.sep(col)[0]
        return b.mixf(r, b.sub(1.0, r), inv_gm)

    GMc, _ = b.image("IMG_GlitterMask", "Glitter Mask (g_tSfxMaskSticker0 A)", uvC_bl, extension='EXTEND')
    gm = glitter_amount(GMc)                                   # _16037 = 1 - sfx.a
    gs = b.mul(2.5, I("Glitter Scale"))                        # _10825
    g_xy = b.vscale(uvC, gs)
    g_zw = b.vscale(b.vadd(uvC, (0.5, 0.5, 0.0)), gs)
    G1c, _ = b.image("IMG_GlitterPoint", "Glitter Normal, point (g_tGlitterNormalSticker0 RG)", to_bl(g_xy), 'Closest', 'REPEAT')
    G1m, _ = b.image("IMG_GlitterNMaskPoint", "Glitter Normal Mask, point (g_tGlitterNormalSticker0 A)", to_bl(g_xy), 'Closest', 'REPEAT')
    G1a = b.sep(G1m)[0]                                        # _19374.w
    g1 = decode(G1c, 0.0, 0.0)                                 # _16013 (always hemi-oct)
    g1s = b.vscale(g1, b.sep(g1)[2])                           # _22292
    s1 = b.vsin(b.vscale(b.vreflect(V, b.normal_map(b.flip_y(g1s))), 12.0))
    sp1 = sparkle(s1)                                          # _17144
    f_g = b.mul(0.03999999910593033, gm)                       # _15604 (screen-space fade = 1)
    g1sx, g1sy, _ = b.sep(g1s)
    nx, ny, nz = b.sep(n10176)
    n88x = b.add(nx, b.mul(g1sx, f_g))
    n88y = b.add(ny, b.mul(g1sy, f_g))
    G2c, _ = b.image("IMG_GlitterLinear", "Glitter Normal, linear (g_tGlitterNormalSticker0 RG)", to_bl(g_zw), 'Linear', 'REPEAT')
    G2m, _ = b.image("IMG_GlitterNMaskLinear", "Glitter Normal Mask, linear (g_tGlitterNormalSticker0 A)", to_bl(g_zw), 'Linear', 'REPEAT')
    G2a = b.sep(G2m)[0]                                        # _23793
    g2 = decode(G2c, 0.0, 0.0)                                 # _16014
    s2 = b.vsin(b.vscale(b.vreflect(V, b.normal_map(b.flip_y(g2))), 12.0))
    sp2 = sparkle(s2)                                          # _17145
    fit_g = b.vmix(S, fit(S, S, b.lum(b.vsat(S)), ALBEDO_LEVELS, ALBEDO_LEVELS[0], 1.0, 1.0), auto_f)  # _9756
    g2x, g2y, _ = b.sep(g2)
    fg2 = b.mul(f_g, G2a)
    n89x = b.add(n88x, b.mul(g2x, fg2))
    n89y = b.add(n88y, b.mul(g2y, fg2))
    glit_c = b.vscale(b.vmul(b.vmax(b.vscale(sp1, G1a), b.vscale(sp2, G2a)),
                             b.vscale(fit_g, I("SFX Color Boost"))), gm)          # _13137
    glit_n = b.comb(n89x, n89y, nz)                            # _20489
    glit_m = b.max(metal_flag, b.mul(gm, 0.5))                 # _16479
    gf = I("Glitter")
    glitter_color = b.vscale(glit_c, gf)                       # _13138
    n16344 = b.vmix(n10176, glit_n, gf)
    m_g = b.mixf(metal_flag, glit_m, gf)                       # _16864
    # Foil with a mask (Singapore 2026+): the inverted glitter mask (1 - g_tSfxMaskSticker0.a) marks
    # the metal foil areas: metal amount = max(_16864, 1 - sfx.a). Used for tint, levels, metalness.
    m = b.mixf(m_g, b.max(m_g, gm), I("Foil Using Glitter Mask"))   # _12889

    # ------------------------------------------------------------------ sticker color (_12889)
    c = S
    sat_s = b.sub(1.0, I("Tint Saturate"))                     # Expression(1-this)
    tint = I("Sticker Tint")
    leg = I("Legacy Tint Multiply")
    boost = I("Color Boost")
    tint_m = b.vmix((1.0, 1.0, 1.0), tint, m)                  # _13747
    sat_m = b.mul(m, sat_s)                                    # _11982
    lum_c = b.lum(c)
    c2 = b.vsat(b.vmul(b.vmix(c, lum_c, sat_m), tint_m))       # legacy: _16474
    n_in_nl = b.vmix(b.vmul(tint_m, c), tint_m, sat_m)         # non legacy fit input
    n_in = b.vmix(n_in_nl, c2, leg)
    cc = b.vmix(c, c2, leg)
    l = b.lum(b.vsat(b.vscale(cc, boost)))                     # _6670 / _6671
    lv = b.vmix(ALBEDO_LEVELS,
                b.vmix(METALLIC_LEVELS, DARK_METALLIC_LEVELS, b.smoothstep(0.550000011920929, 0.015599999576807022, l)),
                m)                                             # _12755 / _12756
    lx = b.mixf(ALBEDO_LEVELS[0], DARK_METALLIC_LEVELS[0], m)  # _20079 / _20080
    auto_out = fit(n_in, cc, l, lv, lx, boost, 1.0)
    metal_out = b.vscale(b.vmul(b.vmix(c, lum_c, sat_s), tint), b.mixf(1.0, boost, leg))  # _24687
    non_auto = b.vmix(c, metal_out, metal_flag)
    col = b.vmix(non_auto, auto_out, auto_f)                   # _12889

    self_illum = b.vscale(col, b.mul(b.mul(self_mask, 2.0), I("Self Illum")))   # _12890

    # ------------------------------------------------------------------ holographic
    # The game blends point- and aniso-sampled holo mask/spectrum by the stored glitter mask
    # (_7714, _25247). Here the holo always takes the smooth (no-glitter) path, so the glitter
    # mask only drives the glitter.
    SFXc, _ = b.image("IMG_SfxLinear", "Holo Mask (g_tSfxMaskSticker0 RGB)", uvC_bl, extension='EXTEND')
    h = SFXc                                                   # _7714 with _19338.w = 1
    hx, hy, hz = b.sep(h)
    # direction TO the sun; driven by the material's "Sun" object (see apply_sun_driver)
    sun_node = b.node('ShaderNodeCombineXYZ', hide=False, name=SUN_NODE, label="Sun Direction (driven)")
    for i, v in enumerate((0.3535534, 0.3535534, 0.8660254)):
        sun_node.inputs[i].default_value = v
    sun = b.vnorm(sun_node.outputs[0])
    cu = b.add(hy, b.add(b.vdot(V, Ngeo), b.vdot(V, sun)))     # _20626.x
    u_w = b.fract(cu)
    v_w = b.fract(hz)
    v_s = b.mixf(v_w, b.sat(hz), I("Clamp Spectrum V"))        # g_sAnisoClampV vs g_sAniso
    SPL, _ = b.image("IMG_SpectrumLinear", "Holo Spectrum (g_tHoloSpectrumSticker0)", b.comb(u_w, b.sub(1.0, v_s), 0.0), 'Smart', 'EXTEND')
    spec = SPL                                                 # _25247 with _19338.w = 1
    sfx_boost = I("SFX Color Boost")
    lh = b.lum(b.vsat(b.vscale(spec, sfx_boost)))              # _7244
    holo = fit(spec, spec, lh, HOLO_LEVELS, HOLO_LEVELS[0], sfx_boost, sfx_boost)
    hxf = b.mul(hx, I("Holographic"))
    col_h = b.vmix(col, holo, hxf)                             # _7479
    m_h = b.mul(m, b.sub(1.0, hxf))                            # _12015

    # ------------------------------------------------------------------ wear
    w = I("Wear")                                              # _16343
    wear_on = b.gt(w, 0.0)
    SCc, _ = b.image("IMG_Scratches", "Sticker Scratches (g_tStickerScratches)", uvC_bl, extension='EXTEND')
    s = b.sub(1.0, b.min(I("Wear Scratches"), b.sep(SCc)[0]))  # _12594
    s2w = b.mixf(s, b.mul(s, 0.5), w)                          # _23653
    ws = b.sat(w)                                              # _13560
    pb = I("Paper Backing")
    by = I("Wear Bias Y")
    bx = I("Wear Bias X")
    t09 = b.mixf(b.mul(b.add(b.mul(by, b.add(0.25, b.mul(0.25, b.gt(by, 0.0)))), 0.5), 0.5), 0.5, pb)  # _20309
    k81 = b.sat(b.sub(ws, b.pow(b.sat(b.mul(b.sub(Sa, 0.0784313753247261), 1.0851063728332520)), b.mul(bx, bx))))  # _8281
    k05 = b.add(b.sat(b.mul(k81, 2.0)), b.mul(by, ws))         # _12205
    paper = b.vmix(b.mixf(0.699999988079071, b.add(0.20000000298023224, b.mul(0.4000000059604645, s2w)), ws),
                   col_h, b.smoothstep(k05, b.add(k05, 0.10000000149011612), s2w))
    col05 = b.vmix(col_h, paper, pb)                           # _12005
    k53 = b.sat(b.sub(b.mul(k81, b.add(1.0, t09)), t09))       # _18653
    rough_np = b.mixf(rough_s, 0.800000011920929, b.step(s2w, b.smoothstep(0.0, 0.3499999940395355, ws)))
    rough_w = b.mixf(rough_np, rough_s, b.max(metal_flag, I("Preserve Roughness")))  # _12503
    col_w = b.vmix(col05, b.vscale(col05, s2w), b.mul(ws, 0.30000001192092896))
    alpha_w = b.mul(a2, b.smoothstep(k53, b.add(k53, 0.10000000149011612), s2w))
    rough39 = b.mixf(rough_s, rough_w, wear_on)                # _14139
    col11 = b.vmix(col_h, col_w, wear_on)                      # _14611.rgb
    alpha11 = b.mixf(a2, alpha_w, wear_on)                     # _14611.w

    # ------------------------------------------------------------------ normal blend (_17845)
    nhx, nhy, nhz = b.sep(n16344)
    n17845 = b.comb(b.mixf(b.sub(uc, 0.5), nhx, alpha11), b.mixf(b.sub(vc, 0.5), nhy, alpha11), nhz)
    f = b.mul(a, alpha11)                                      # _12751 / _15651

    # ------------------------------------------------------------------ final composite
    # In the game the sticker is blended onto the surface underneath by its coverage:
    #   _17129 = mix(_23987, saturate(col), cov)   _17130 = mix(base rough, rough, cov)
    #   _17131 = mix(base metal, metal, cov)        _17133/_16310 = emission/glitter * cov
    # Here the surface underneath is whatever is behind the decal, so the coverage is applied
    # exactly once, by the transparent mix below. Blending with the base layer as well would
    # apply it twice and wash partly covered areas out towards white (albedo0) and the base
    # roughness. So the BSDF and the outputs carry the sticker's own, unblended values.
    fa = b.mul(f, apply)                                       # coverage (_15651), used once
    albedo = b.vsat(col11)                                     # saturate(_11402.rgb)
    rough = rough39                                            # _16308
    metal = m_h                                                # _17120
    emis = self_illum                                          # _12890
    glit = glitter_color                                       # _13138
    w_final = b.vmix(w_base, b.vnorm(n17845), apply)           # _17121 (sticker normal)
    N_final = b.normal_map(b.flip_y(w_final))                  # _14788

    base_col = albedo
    glit_col = glit

    p = b.node('ShaderNodeBsdfPrincipled', hide=False)
    try:
        p.distribution = 'GGX'
    except TypeError:
        pass
    b.feed(p.inputs['Base Color'], base_col)
    b.feed(p.inputs['Metallic'], metal)
    b.feed(p.inputs['Roughness'], rough)
    b.feed(p.inputs['Normal'], N_final)
    b.feed(p.inputs['IOR'], 1.5)  # F0 = ((1.5-1)/(1.5+1))^2 = 0.04 = g_flReflectance
    if 'Specular IOR Level' in p.inputs:
        b.feed(p.inputs['Specular IOR Level'], 0.5)
    emis_in = p.inputs.get('Emission Color') or p.inputs.get('Emission')
    b.feed(emis_in, emis)
    if 'Emission Strength' in p.inputs:
        b.feed(p.inputs['Emission Strength'], 1.0)

    # glitter is added on top, lit by the diffuse irradiance:  lit += _17068 * _16310
    dif = b.node('ShaderNodeBsdfDiffuse', hide=False)
    b.feed(dif.inputs['Color'], glit_col)
    b.feed(dif.inputs['Normal'], N_final)
    add = b.node('ShaderNodeAddShader', hide=False)
    b.feed(add.inputs[0], p.outputs[0])
    b.feed(add.inputs[1], dif.outputs[0])

    # The weapon's sticker mask (red channel of *_sticker_mask_hd or _legacy) limits where stickers may appear.
    fa = b.mul(fa, b.sat(I("Weapon Sticker Mask")))
    # Outside the sticker coverage: transparent (sticker planes), or the weapon's own shader when the
    # sticker is layered over it inside the weapon material. The coverage is applied exactly once.
    clear = b.node('ShaderNodeBsdfTransparent', hide=False)
    under = b.node('ShaderNodeMixShader', hide=False)
    b.feed(under.inputs[0], I("Layer Over Base Shader"))
    b.feed(under.inputs[1], clear.outputs[0])
    b.feed(under.inputs[2], I("Base Shader"))
    mixs = b.node('ShaderNodeMixShader', hide=False)
    b.feed(mixs.inputs[0], fa)
    b.feed(mixs.inputs[1], under.outputs[0])
    b.feed(mixs.inputs[2], add.outputs[0])

    b.feed(go.inputs["BSDF"], mixs.outputs[0])
    b.feed(go.inputs["Base Color"], albedo)
    b.feed(go.inputs["Metallic"], metal)
    b.feed(go.inputs["Roughness"], rough)
    b.feed(go.inputs["Normal"], N_final)
    b.feed(go.inputs["Emission"], emis)
    b.feed(go.inputs["Glitter"], glit)
    b.feed(go.inputs["Sticker Mask"], fa)

    auto_layout(g)
    g["cs2_version"] = VERSION
    return g
