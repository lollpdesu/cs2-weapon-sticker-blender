bl_info = {
    "name": "CS2 Weapon Sticker Shader",
    "author": "Lollp",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "Properties > Material > CS2 Weapon Sticker, 3D Viewport > Sidebar > CS2 Sticker",
    "description": "Node-based recreation of the CS2 csgo_weapon_sticker shader, with a browser that imports stickers straight from the game",
    "category": "Material",
}

import colorsys
import bpy
from bpy.props import EnumProperty, PointerProperty

from . import nodes_build as nb

GROUP_NODE_NAME = "CS2 Weapon Sticker"


# ====================================================================== texture slots

class Slot:
    def __init__(self, prop, label, nodes, colorspace, default, hemi_default=None, toggle=None):
        self.prop = prop
        self.label = label
        self.nodes = nodes
        self.colorspace = colorspace
        self.default = default            # default image key when the slot is empty
        self.hemi_default = hemi_default  # alternative default when using hemi-oct normals
        self.toggle = toggle              # group input drawn under the slot


SLOTS = [
    Slot("tex_rough", "Base Roughness", ["IMG_Rough"], 'Non-Color', "rough_base"),
    Slot("tex_sticker", "Sticker Color", ["IMG_Sticker"], 'sRGB', "clear"),
    Slot("tex_wear_mask", "Wear Mask (empty = sticker alpha)", ["IMG_WearMask", "IMG_WearMaskEdge"], 'Non-Color', None),
    Slot("tex_sticker_nr", "Sticker Normal", ["IMG_StickerNR"], 'Non-Color', "normal_std", hemi_default="normal_hemi"),
    Slot("tex_sticker_rough", "Sticker Roughness (empty = 0.1)", ["IMG_StickerRough"], 'Non-Color', "rough_sticker"),
    Slot("tex_self_illum", "Self Illum Mask", ["IMG_SelfIllum"], 'Non-Color', "black"),
    Slot("tex_holo_mask", "Holo Mask (R mask, G/B spectrum offset)", ["IMG_SfxLinear"], 'Non-Color', "black"),
    Slot("tex_spectrum", "Holo Spectrum", ["IMG_SpectrumLinear"], 'sRGB', "spectrum"),
    Slot("tex_glitter_mask", "Glitter Mask (white = glitter)", ["IMG_GlitterMask"], 'Non-Color', "black", toggle="Invert Glitter Mask"),
    Slot("tex_glitter_normal", "Glitter Normal (RG)", ["IMG_GlitterPoint", "IMG_GlitterLinear"], 'Non-Color', "glitter"),
    Slot("tex_glitter_normal_mask", "Glitter Normal Mask", ["IMG_GlitterNMaskPoint", "IMG_GlitterNMaskLinear"], 'Non-Color', "white"),
    Slot("tex_scratches", "Sticker Scratches (R)", ["IMG_Scratches"], 'Non-Color', "black"),
]
SLOT_BY_PROP = {s.prop: s for s in SLOTS}

_H = 128.0 / 255.0  # flat hemi-octahedral normal


def _default_pixels(key):
    """(width, height, colorspace, rgba pixel list)"""
    flat = {
        "white": (1.0, 1.0, 1.0, 1.0),
        "black": (0.0, 0.0, 0.0, 1.0),
        "rough_base": (0.5, 0.5, 0.5, 1.0),
        "rough_sticker": (0.1, 0.1, 0.1, 1.0),
        "clear": (1.0, 1.0, 1.0, 0.0),
        "normal_hemi": (_H, _H, 0.5, 1.0),
        "normal_std": (0.5, 0.5, 1.0, 1.0),
        "glitter": (_H, _H, 0.0, 1.0),
    }
    if key in flat:
        cs = 'sRGB' if key in {"white", "clear"} else 'Non-Color'
        return 1, 1, cs, list(flat[key])
    if key == "spectrum":
        w, h = 256, 4
        px = []
        for _y in range(h):
            for x in range(w):
                r, g, b_ = colorsys.hsv_to_rgb(x / w, 0.85, 1.0)
                px += [r, g, b_, 1.0]
        return w, h, 'sRGB', px
    raise KeyError(key)


def default_image(key):
    name = "CS2_Default_" + key
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    w, h, cs, px = _default_pixels(key)
    img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
    _set_colorspace(img, cs)
    img.alpha_mode = 'CHANNEL_PACKED'
    img.pixels = px
    try:
        img.pack()
    except RuntimeError:
        pass
    return img


def _set_colorspace(img, cs):
    try:
        img.colorspace_settings.name = cs
    except TypeError:
        if cs == 'Non-Color':
            try:
                img.colorspace_settings.is_data = True
            except AttributeError:
                pass


# ====================================================================== material helpers

def find_group_node(mat):
    if mat is None or mat.node_tree is None:
        return None
    s = mat.cs2_sticker
    for n in mat.node_tree.nodes:
        if n.bl_idname == 'ShaderNodeGroup' and n.node_tree is not None and (
                n.node_tree == s.group or n.node_tree.name.startswith(nb.MAIN_PREFIX)):
            return n
    return None


def _uses_hemi(settings):
    return settings.normal_format == 'HEMIOCT'


def refresh_wear_mask(mat):
    """Wear mask nodes read the wear mask texture (R), or the sticker color's alpha if none is set."""
    s = mat.cs2_sticker
    g = s.group
    if g is None:
        return
    wm = s.tex_wear_mask
    if wm is not None:
        if wm.colorspace_settings.name != 'Non-Color':
            _set_colorspace(wm, 'Non-Color')
        img, from_alpha = wm, 0.0
    else:
        img, from_alpha = (s.tex_sticker or default_image("clear")), 1.0
    for name in SLOT_BY_PROP["tex_wear_mask"].nodes:
        node = g.nodes.get(name)
        if node is not None:
            node.image = img
    node = find_group_node(mat)
    if node is not None and "Wear Mask From Sticker Alpha" in node.inputs:
        node.inputs["Wear Mask From Sticker Alpha"].default_value = from_alpha


def assign_slot(mat, prop):
    s = mat.cs2_sticker
    g = s.group
    if g is None:
        return
    if prop in ("tex_wear_mask", "tex_sticker"):
        refresh_wear_mask(mat)
        if prop == "tex_wear_mask":
            return
    slot = SLOT_BY_PROP[prop]
    img = getattr(s, prop)
    if img is not None:
        # only touch settings that differ: changing them reloads the image buffer
        if img.colorspace_settings.name != slot.colorspace:
            _set_colorspace(img, slot.colorspace)
        if img.alpha_mode != 'CHANNEL_PACKED':
            img.alpha_mode = 'CHANNEL_PACKED'
    else:
        key = slot.hemi_default if (slot.hemi_default and _uses_hemi(s)) else slot.default
        img = default_image(key)
    for name in slot.nodes:
        node = g.nodes.get(name)
        if node is not None:
            node.image = img


def assign_all_slots(mat):
    for slot in SLOTS:
        assign_slot(mat, slot.prop)


def apply_normal_format(mat):
    node = find_group_node(mat)
    s = mat.cs2_sticker
    if node is not None:
        node.inputs["Standard Normal Maps"].default_value = 0.0 if _uses_hemi(s) else 1.0
        node.inputs["DirectX Normals"].default_value = 1.0 if s.normal_format == 'DIRECTX' else 0.0
    assign_slot(mat, "tex_sticker_nr")


def apply_sun_driver(mat):
    node = find_group_node(mat)
    g = mat.cs2_sticker.group
    if node is None or g is None:
        return
    # drop drivers left on the group node's inputs by older versions (input order has changed)
    ad = mat.node_tree.animation_data
    if ad is not None:
        prefix = 'nodes["%s"].inputs' % node.name
        for fc in [fc for fc in ad.drivers if fc.data_path.startswith(prefix)]:
            ad.drivers.remove(fc)
    set_sun_driver(g, mat.cs2_sticker.sun_object)


def set_sun_driver(g, sun):
    """Drive the holo spectrum's light direction inside sticker group `g` from `sun` (None: no driver)."""
    sun_node = g.nodes.get(nb.SUN_NODE)
    if sun_node is None:
        return
    for sock in sun_node.inputs:
        try:
            sock.driver_remove("default_value")
        except (TypeError, RuntimeError):
            pass
    if sun is None:
        return
    # Direction TO the light = the light object's local +Z axis in world space
    for i in range(3):
        fc = sun_node.inputs[i].driver_add("default_value")
        drv = fc.driver
        drv.type = 'AVERAGE'
        for v in list(drv.variables):
            drv.variables.remove(v)
        var = drv.variables.new()
        var.name = "axis"
        var.type = 'SINGLE_PROP'
        var.targets[0].id_type = 'OBJECT'
        var.targets[0].id = sun
        # RNA indexes matrices column-first: [2][i] = row i of the Z column
        var.targets[0].data_path = "matrix_world[2][%d]" % i


# ====================================================================== nested sticker groups
# Stickers placed on CS2 weapons live as sticker groups inside the weapon material (in a
# "CS2 Weapon Stickers" group), not as materials of their own. Their textures are stored on the
# group itself (custom property "cs2_images") so the group can be rebuilt after an update.

NESTED_TAG = "cs2_nested_sticker"


def assign_group_images(g, images):
    """Put `images` ({slot prop: Image}) into sticker group `g`; empty slots get the defaults.
    Returns the "Wear Mask From Sticker Alpha" value to set on the group's node."""
    for slot in SLOTS:
        if slot.prop == "tex_wear_mask":
            continue
        img = images.get(slot.prop)
        if img is not None:
            if img.colorspace_settings.name != slot.colorspace:
                _set_colorspace(img, slot.colorspace)
            if img.alpha_mode != 'CHANNEL_PACKED':
                img.alpha_mode = 'CHANNEL_PACKED'
        else:
            img = default_image(slot.hemi_default or slot.default)   # compiled textures: hemi-oct normals
        for name in slot.nodes:
            node = g.nodes.get(name)
            if node is not None:
                node.image = img
    wm = images.get("tex_wear_mask")
    img, from_alpha = (wm, 0.0) if wm is not None else (images.get("tex_sticker") or default_image("clear"), 1.0)
    for name in SLOT_BY_PROP["tex_wear_mask"].nodes:
        node = g.nodes.get(name)
        if node is not None:
            node.image = img
    return from_alpha


def _group_users(g):
    """(node tree, group node) for every group node that uses `g`."""
    trees = [m.node_tree for m in bpy.data.materials if m.node_tree is not None] + list(bpy.data.node_groups)
    return [(t, n) for t in trees for n in t.nodes if n.bl_idname == 'ShaderNodeGroup' and n.node_tree == g]


def rebuild_nested_group(g):
    """Rebuild a nested sticker group with the current node graph, keeping values, links and textures."""
    saved = []
    for tree, n in _group_users(g):
        values = {}
        for sock in n.inputs:
            if hasattr(sock, "default_value") and not sock.is_linked:
                try:
                    values[sock.name] = tuple(sock.default_value)
                except TypeError:
                    values[sock.name] = sock.default_value
        ins = [(l.from_socket, l.to_socket.name) for l in tree.links if l.to_node == n]
        outs = [(l.from_socket.name, l.to_socket) for l in tree.links if l.from_node == n]
        saved.append((tree, n, values, ins, outs))
    nb.build_main_group(g)
    images = {k: v for k, v in (g.get("cs2_images") or {}).items() if isinstance(v, bpy.types.Image)}
    assign_group_images(g, images)
    set_sun_driver(g, g.get("cs2_sun") if isinstance(g.get("cs2_sun"), bpy.types.Object) else None)
    for tree, n, values, ins, outs in saved:
        for name, val in values.items():
            sock = n.inputs.get(name)
            if sock is not None and hasattr(sock, "default_value"):
                try:
                    sock.default_value = val
                except (TypeError, ValueError):
                    pass
        for from_sock, to_name in ins:
            if to_name in n.inputs:
                tree.links.new(from_sock, n.inputs[to_name])
        for from_name, to_sock in outs:
            if from_name in n.outputs:
                tree.links.new(n.outputs[from_name], to_sock)


def _upd_slot(prop):
    def upd(self, context):
        assign_slot(self.id_data, prop)
    return upd


def _upd_normal_format(self, context):
    apply_normal_format(self.id_data)


def _upd_sun(self, context):
    apply_sun_driver(self.id_data)


def _poll_light(self, obj):
    return obj.type == 'LIGHT'


_ann = {}
for _slot in SLOTS:
    _ann[_slot.prop] = PointerProperty(name=_slot.label, type=bpy.types.Image, update=_upd_slot(_slot.prop))
# Up to 1.0.0 the base (weapon) normal map was a texture slot. It is now the group's "Base Normal"
# input; this property is kept only so older files can be migrated (see _migrate_base_normal).
_ann["tex_normal"] = PointerProperty(type=bpy.types.Image)
_ann["normal_format"] = EnumProperty(
    name="Normal Format",
    items=[
        ('HEMIOCT', "Source 2 (Hemi-Oct RG)", "Raw compiled Source 2 textures: hemi-octahedral normal in RG, roughness in B"),
        ('OPENGL', "Standard RGB (OpenGL)", "Regular tangent-space normal maps, roughness from separate textures"),
        ('DIRECTX', "Standard RGB (DirectX)", "Regular tangent-space normal maps with flipped green, roughness from separate textures"),
    ],
    default='HEMIOCT',
    update=_upd_normal_format,
)
_ann["sun_object"] = PointerProperty(
    name="Sun", type=bpy.types.Object, poll=_poll_light, update=_upd_sun,
    description="Light whose direction drives the holographic spectrum (direct light direction in-game)",
)
_ann["group"] = PointerProperty(type=bpy.types.NodeTree)

CS2StickerSettings = type("CS2StickerSettings", (bpy.types.PropertyGroup,), {"__annotations__": _ann})


def _group_name(mat):
    return "%s [%s]" % (nb.MAIN_PREFIX, mat.name)


def setup_material(mat, scene=None):
    """Add (or rebuild) the sticker group in `mat` and wire it to the material output."""
    try:
        mat.use_nodes = True
    except AttributeError:
        pass
    nt = mat.node_tree
    s = mat.cs2_sticker
    node = find_group_node(mat)

    # the area outside the sticker is transparent
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = 'DITHERED'
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True

    # remember values and links of an existing instance
    values, in_links, out_links = {}, [], []
    if node is not None:
        for sock in node.inputs:
            if hasattr(sock, "default_value") and not sock.is_linked:
                try:
                    values[sock.name] = tuple(sock.default_value)
                except TypeError:
                    values[sock.name] = sock.default_value
        for l in nt.links:
            if l.to_node == node:
                in_links.append((l.from_socket, l.to_socket.name))
            elif l.from_node == node:
                out_links.append((l.from_socket.name, l.to_socket))

    g = s.group
    if g is None:
        g = bpy.data.node_groups.new(_group_name(mat), 'ShaderNodeTree')
        s.group = g
    nb.build_main_group(g)

    if node is None:
        node = nt.nodes.new('ShaderNodeGroup')
        node.name = GROUP_NODE_NAME
        node.label = GROUP_NODE_NAME
        node.width = 260
        node.node_tree = g
        out = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial' and n.is_active_output), None)
        if out is None:
            out = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial'), None)
        if out is None:
            out = nt.nodes.new('ShaderNodeOutputMaterial')
        node.location = (out.location.x - 340, out.location.y)
        nt.links.new(node.outputs["BSDF"], out.inputs["Surface"])
        uvn = nt.nodes.new('ShaderNodeUVMap')
        uvn.location = (node.location.x - 220, node.location.y - 40)
        nt.links.new(uvn.outputs["UV"], node.inputs["UV"])
        nt.links.new(uvn.outputs["UV"], node.inputs["Sticker UV"])
        if scene is not None and s.sun_object is None:
            sun = next((o for o in scene.objects if o.type == 'LIGHT' and o.data.type == 'SUN'), None)
            if sun is not None:
                s.sun_object = sun  # triggers driver setup
    else:
        node.node_tree = g
        for name, val in values.items():
            sock = node.inputs.get(name)
            if sock is not None and hasattr(sock, "default_value"):
                try:
                    sock.default_value = val
                except (TypeError, ValueError):
                    pass
        for from_sock, to_name in in_links:
            if to_name in node.inputs:
                nt.links.new(from_sock, node.inputs[to_name])
        for from_name, to_sock in out_links:
            if from_name in node.outputs:
                nt.links.new(node.outputs[from_name], to_sock)

    assign_all_slots(mat)
    apply_normal_format(mat)
    apply_sun_driver(mat)
    _migrate_base_normal(mat, node)
    return node


def _migrate_base_normal(mat, node):
    """Move a base normal map from the old texture slot to an Image Texture node on the "Base Normal" input."""
    s = mat.cs2_sticker
    img = s.tex_normal
    sock = node.inputs.get("Base Normal")
    if img is None or sock is None:
        return
    nt = mat.node_tree
    if not sock.is_linked:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.label = "Base Normal"
        tex.image = img
        _set_colorspace(img, "Non-Color")
        tex.location = (node.location.x - 300, node.location.y - 260)
        uv_link = next((l for l in nt.links if l.to_node == node and l.to_socket.name == "UV"), None)
        if uv_link is not None:
            nt.links.new(uv_link.from_socket, tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], sock)
        # the old slot used the material's normal format
        node.inputs["Base Normal Hemi-Oct"].default_value = 1.0 if s.normal_format == 'HEMIOCT' else 0.0
        node.inputs["Base Normal DirectX"].default_value = 1.0 if s.normal_format == 'DIRECTX' else 0.0
    s.tex_normal = None


# ====================================================================== operators

class CS2STICKER_OT_new_material(bpy.types.Operator):
    bl_idname = "cs2sticker.new_material"
    bl_label = "New CS2 Sticker Material"
    bl_description = "Create a new material using the CS2 weapon sticker shader and assign it to the active slot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and hasattr(ob.data, "materials")

    def execute(self, context):
        ob = context.object
        mat = bpy.data.materials.new("CS2 Weapon Sticker")
        try:
            mat.use_nodes = True
        except AttributeError:
            pass
        nt = mat.node_tree
        for n in list(nt.nodes):
            if n.bl_idname != 'ShaderNodeOutputMaterial':
                nt.nodes.remove(n)
        setup_material(mat, context.scene)
        if len(ob.material_slots) == 0:
            ob.data.materials.append(mat)
        else:
            ob.material_slots[ob.active_material_index].material = mat
        return {'FINISHED'}


class CS2STICKER_OT_setup_material(bpy.types.Operator):
    bl_idname = "cs2sticker.setup_material"
    bl_label = "Add Sticker Shader to Material"
    bl_description = "Add the CS2 sticker shader to the current material and connect it to the output (other nodes are kept)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def execute(self, context):
        setup_material(context.material, context.scene)
        return {'FINISHED'}


class CS2STICKER_OT_rebuild(bpy.types.Operator):
    bl_idname = "cs2sticker.rebuild"
    bl_label = "Rebuild Node Tree"
    bl_description = "Regenerate the sticker node group (keeps parameters, links and textures)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material is not None and find_group_node(context.material) is not None

    def execute(self, context):
        for name in (nb.FIT_NAME, nb.SPARKLE_NAME, nb.DECODE_NAME):
            g = bpy.data.node_groups.get(name)
            if g is not None:
                g["cs2_version"] = -1
        setup_material(context.material, context.scene)
        return {'FINISHED'}


# ====================================================================== UI

SECTIONS = [
    ("Sticker Color", ["Enable Sticker", "Sticker Tint", "Tint Saturate", "Automatic PBR Color Fitting",
                       "Legacy Tint Multiply", "Color Boost", "Metallic"]),
    ("SFX", ["Holographic", "Clamp Spectrum V", "SFX Color Boost", "Foil Using Glitter Mask", "Glitter", "Glitter Scale", "Self Illum"]),
    ("Wear", ["Wear", "Wear Bias X", "Wear Bias Y", "Wear Scratches", "Paper Backing", "Preserve Roughness"]),
    ("Base Weapon", ["Base Normal", "Base Normal Hemi-Oct", "Base Normal DirectX"]),
    ("Sticker Placement", ["UV Placement", "Slot X", "Slot Y", "Slot Scale", "Rotation"]),
]


class CS2STICKER_PT_main(bpy.types.Panel):
    bl_label = "CS2 Weapon Sticker"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        layout = self.layout
        mat = context.material
        if mat is None or find_group_node(mat) is None:
            layout.operator(CS2STICKER_OT_new_material.bl_idname, icon='ADD')
            if mat is not None:
                layout.operator(CS2STICKER_OT_setup_material.bl_idname, icon='NODETREE')
            return
        s = mat.cs2_sticker
        layout.use_property_split = True
        layout.prop(s, "normal_format")
        layout.prop(s, "sun_object")
        layout.operator(CS2STICKER_OT_rebuild.bl_idname, icon='FILE_REFRESH')


class CS2STICKER_PT_textures(bpy.types.Panel):
    bl_label = "Textures"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_parent_id = "CS2STICKER_PT_main"

    @classmethod
    def poll(cls, context):
        return find_group_node(context.material) is not None

    def draw(self, context):
        s = context.material.cs2_sticker
        node = find_group_node(context.material)
        col = self.layout.column()
        for slot in SLOTS:
            col.label(text=slot.label)
            col.template_ID(s, slot.prop, open="image.open")
            if slot.toggle and slot.toggle in node.inputs:
                col.prop(node.inputs[slot.toggle], "default_value", text=slot.toggle, slider=True)
            col.separator()


def _make_param_panel(section, names):
    def draw(self, context):
        node = find_group_node(context.material)
        layout = self.layout
        layout.use_property_split = True
        for name in names:
            sock = node.inputs.get(name)
            if sock is None:
                continue
            if sock.is_linked:
                layout.label(text="%s: linked" % name)
            else:
                layout.prop(sock, "default_value", text=name, slider=(name in nb.FLAG_INPUTS))

    ident = "CS2STICKER_PT_" + section.lower().replace(" ", "_")
    return type(ident, (bpy.types.Panel,), {
        "bl_label": section,
        "bl_idname": ident,
        "bl_space_type": 'PROPERTIES',
        "bl_region_type": 'WINDOW',
        "bl_context": "material",
        "bl_parent_id": "CS2STICKER_PT_main",
        "bl_options": {'DEFAULT_CLOSED'} if section != "Sticker Color" else set(),
        "poll": classmethod(lambda cls, context: find_group_node(context.material) is not None),
        "draw": draw,
    })


PARAM_PANELS = [_make_param_panel(sec, names) for sec, names in SECTIONS]

classes = [
    CS2StickerSettings,
    CS2STICKER_OT_new_material,
    CS2STICKER_OT_setup_material,
    CS2STICKER_OT_rebuild,
    CS2STICKER_PT_main,
    CS2STICKER_PT_textures,
    *PARAM_PANELS,
]


def upgrade_outdated_materials():
    """Rebuild sticker groups made by an older version of the node graph (values and textures are kept)."""
    count = 0
    for mat in bpy.data.materials:
        s = getattr(mat, "cs2_sticker", None)
        g = s.group if s is not None else None
        if g is None or g.get("cs2_version") == nb.VERSION or find_group_node(mat) is None:
            continue
        try:
            setup_material(mat)
            count += 1
        except Exception as e:
            print("CS2 sticker: could not upgrade %s: %s" % (mat.name, e))
    for g in list(bpy.data.node_groups):
        if g.get(NESTED_TAG) and g.get("cs2_version") != nb.VERSION:
            try:
                rebuild_nested_group(g)
                count += 1
            except Exception as e:
                print("CS2 sticker: could not upgrade %s: %s" % (g.name, e))
    if count:
        print("CS2 sticker: updated the node graph of %d material(s)" % count)
    return count


@bpy.app.handlers.persistent
def _on_load_post(_dummy=None):
    upgrade_outdated_materials()


def _upgrade_current_file():
    try:
        upgrade_outdated_materials()
    except Exception as e:
        print("CS2 sticker: upgrade failed:", e)
    return None


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Material.cs2_sticker = PointerProperty(type=CS2StickerSettings)
    from . import browser
    browser.register()
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.timers.register(_upgrade_current_file, first_interval=0.1)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    from . import browser
    browser.unregister()
    del bpy.types.Material.cs2_sticker
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
