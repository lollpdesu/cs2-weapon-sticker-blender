"""Stickers on CS2 weapon models, layered inside the weapon's own material.

The weapon material gets one "CS2 Weapon Stickers" group between its shader and the Material Output:

    weapon shader -> [BSDF]  CS2 Weapon Stickers  [Shader] -> Material Output
    sticker mask  -> [Mask]
    normal map    -> [NormalMap]

Inside, every sticker slot is its own sticker group ("Slot 1 | ...", "Slot 2 | ..."), chained so each
one is drawn over the result of the previous ones. The sticker UV comes from the weapon's second UV
map with the slot's position, and the weapon's sticker mask limits where stickers may appear.
"""

import bpy

from . import nodes_build as nb
from . import weapon_slots as ws

WRAPPER_TAG = "cs2_weapon_stickers"
WRAPPER_NAME = "CS2 Weapon Stickers"
MASK_NODE = "CS2 Sticker Mask"
MASK_UV_NODE = "CS2 Sticker Mask UV"
UV_NODE = "Sticker UV"
SEP_NODE = "Mask R"


def find_wrapper_node(mat):
    if mat is None or mat.node_tree is None:
        return None
    for n in mat.node_tree.nodes:
        if n.bl_idname == 'ShaderNodeGroup' and n.node_tree is not None and n.node_tree.get(WRAPPER_TAG):
            return n
    return None


def _new_wrapper_group(mat):
    wg = bpy.data.node_groups.new("%s [%s]" % (WRAPPER_NAME, mat.name), 'ShaderNodeTree')
    wg[WRAPPER_TAG] = 1
    wg.interface.new_socket("Shader", in_out='OUTPUT', socket_type='NodeSocketShader')
    wg.interface.new_socket("BSDF", in_out='INPUT', socket_type='NodeSocketShader',
                            description="The weapon's own shader; the stickers are drawn over it")
    s = wg.interface.new_socket("Mask", in_out='INPUT', socket_type='NodeSocketColor',
                                description="Weapon sticker mask (red channel of *_sticker_mask_hd, or *_sticker_mask_legacy on legacy models). White = stickers allowed")
    s.default_value = (1.0, 1.0, 1.0, 1.0)
    s = wg.interface.new_socket("NormalMap", in_out='INPUT', socket_type='NodeSocketColor',
                                description="The weapon's normal map (Image Texture, Non-Color). Stickers follow its bumps")
    s.default_value = (0.5, 0.5, 1.0, 1.0)
    gi = wg.nodes.new('NodeGroupInput')
    go = wg.nodes.new('NodeGroupOutput')
    uv = wg.nodes.new('ShaderNodeUVMap')
    uv.name = uv.label = UV_NODE
    sep = wg.nodes.new('ShaderNodeSeparateColor')
    sep.name = sep.label = SEP_NODE
    wg.links.new(gi.outputs["Mask"], sep.inputs["Color"])
    wg.links.new(gi.outputs["BSDF"], go.inputs["Shader"])
    return wg


def ensure_wrapper(mat, uv_name):
    """The weapon material's "CS2 Weapon Stickers" node, inserted before the Material Output if new."""
    nt = mat.node_tree
    node = find_wrapper_node(mat)
    if node is None:
        wg = _new_wrapper_group(mat)
        out = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial' and n.is_active_output), None)
        if out is None:
            out = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial'), None)
        if out is None:
            out = nt.nodes.new('ShaderNodeOutputMaterial')
        prev = next((l.from_socket for l in nt.links if l.to_node == out and l.to_socket.name == "Surface"), None)
        node = nt.nodes.new('ShaderNodeGroup')
        node.node_tree = wg
        node.name = node.label = WRAPPER_NAME
        node.width = 200
        node.location = (out.location.x, out.location.y)
        out.location.x += 280
        if prev is not None:
            nt.links.new(prev, node.inputs["BSDF"])
        nt.links.new(node.outputs["Shader"], out.inputs["Surface"])
    node.node_tree.nodes[UV_NODE].uv_map = uv_name
    return node


def relink(wg):
    """Chain the slot groups in slot order and connect the shared inputs."""
    gi = next(n for n in wg.nodes if n.bl_idname == 'NodeGroupInput')
    go = next(n for n in wg.nodes if n.bl_idname == 'NodeGroupOutput')
    uv, sep = wg.nodes[UV_NODE], wg.nodes[SEP_NODE]
    slots = sorted((n for n in wg.nodes if n.get("cs2_slot")), key=lambda n: n["cs2_slot"])
    gi.location = (-600, 0)
    uv.location = (-600, -260)
    sep.location = (-600, -380)
    prev = gi.outputs["BSDF"]
    for i, n in enumerate(slots):
        wg.links.new(prev, n.inputs["Base Shader"])
        wg.links.new(uv.outputs["UV"], n.inputs["Sticker UV"])
        wg.links.new(gi.outputs["NormalMap"], n.inputs["Base Normal"])
        wg.links.new(sep.outputs[0], n.inputs["Weapon Sticker Mask"])
        n.location = (-300 + i * 320, 200)
        prev = n.outputs["BSDF"]
    wg.links.new(prev, go.inputs["Shader"])
    go.location = (-300 + len(slots) * 320, 0)


def set_slot(wrapper_node, slot, sticker_group, label):
    """Put `sticker_group` into `slot`, replacing a sticker that was there."""
    wg = wrapper_node.node_tree
    for n in list(wg.nodes):
        if n.get("cs2_slot") == slot:
            old = n.node_tree
            wg.nodes.remove(n)
            if old is not None and old.users == 0:
                bpy.data.node_groups.remove(old)
    n = wg.nodes.new('ShaderNodeGroup')
    n.node_tree = sticker_group
    n["cs2_slot"] = slot
    n.name = "Slot %d" % slot
    n.label = label
    n.width = 260
    relink(wg)
    return n


def apply_mask(mat, wrapper_node, image, uv_name, weapon_label):
    """Connect the weapon's sticker mask image (first UV map) to the wrapper's Mask input."""
    nt = mat.node_tree
    tex = nt.nodes.get(MASK_NODE)
    if tex is None:
        if wrapper_node.inputs["Mask"].is_linked:
            return False  # the user wired their own mask: leave it
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.name = MASK_NODE
        tex.location = (wrapper_node.location.x - 320, wrapper_node.location.y - 220)
    tex.label = "Sticker Mask (%s)" % weapon_label
    tex.image = image
    uvn = nt.nodes.get(MASK_UV_NODE)
    if uvn is None:
        uvn = nt.nodes.new('ShaderNodeUVMap')
        uvn.name = MASK_UV_NODE
        uvn.label = "Weapon UV"
        uvn.location = (tex.location.x - 200, tex.location.y)
    uvn.uv_map = uv_name
    nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], wrapper_node.inputs["Mask"])
    return True


def place_sticker(context, weapon_obj, key, slot, slot_data, uv_name, sticker, state, wear=None, legacy=None,
                  slot_label=None):
    """Layer `sticker` into slot `slot` (game slot number) of the weapon's active material, placed with
    `slot_data` (a slot entry from cs2data.read_sticker_slots).
    Returns (material, slot node, mask found)."""
    from . import assign_group_images, set_sun_driver, NESTED_TAG
    from . import browser as br

    mat = weapon_obj.active_material
    if mat is None:
        mat = bpy.data.materials.new(weapon_obj.name)
        try:
            mat.use_nodes = True
        except AttributeError:
            pass
        if len(weapon_obj.material_slots) == 0:
            weapon_obj.data.materials.append(mat)
        else:
            weapon_obj.material_slots[weapon_obj.active_material_index].material = mat
    try:
        mat.use_nodes = True
    except AttributeError:
        pass

    _sid, display, mat_path, _coll, _typ = sticker
    vmat, images, _aspect = br.load_sticker(context, sticker)

    label = "%s | %s" % (slot_label or "Slot %d" % slot, display)
    g = bpy.data.node_groups.new("CS2 Sticker " + label, 'ShaderNodeTree')
    nb.build_main_group(g)
    g[NESTED_TAG] = 1
    g["cs2_images"] = images
    g["cs2_sticker_material"] = mat_path
    sun = next((o for o in context.scene.objects if o.type == 'LIGHT' and o.data.type == 'SUN'), None)
    if sun is not None:
        g["cs2_sun"] = sun
    set_sun_driver(g, sun)
    from_alpha = assign_group_images(g, images)

    wrapper = ensure_wrapper(mat, uv_name)
    node = set_slot(wrapper, slot, g, label)
    br.apply_material_values(node, vmat, images, state.wear if wear is None else wear)
    x, y, scale, rot = slot_data["x"], slot_data["y"], slot_data["scale"], slot_data["rotation"]
    for name, val in (("Wear Mask From Sticker Alpha", from_alpha), ("Layer Over Base Shader", 1.0),
                      ("UV Placement", 1.0), ("Slot X", x), ("Slot Y", y), ("Slot Scale", scale), ("Rotation", rot)):
        node.inputs[name].default_value = val

    mask_found = False
    if legacy is None:
        legacy = getattr(state, "model", 'HD') == 'LEGACY'
    mask_img = br.load_weapon_mask(context, key, legacy)
    if mask_img is not None:
        uv0 = weapon_obj.data.uv_layers[0].name if len(weapon_obj.data.uv_layers) else ""
        apply_mask(mat, wrapper, mask_img, uv0, ws.display(key) + (" Legacy" if legacy else ""))
        mask_found = True
    return mat, node, mask_found
