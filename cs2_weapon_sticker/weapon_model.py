"""Import a weapon model straight from the CS2 game files (used by the Console Link import).

CS2 weapon models (weapons/models/<weapon>/weapon_*.vmdl_c) embed two meshes: "body_hd" (the
CS2 model) and "body_legacy" (the legacy model used by legacy finishes). Only the one that matches
is imported. The mesh keeps both UV maps (TEXCOORD_0 for the weapon textures and the sticker
mask, TEXCOORD_1 for sticker placement) and the game's normals.

Orientation and size match Source2Viewer's glTF export (metres, barrel along -Y), so models
imported here and models exported with Source2Viewer line up.
"""

import os

import bpy
import numpy as np

from . import cs2data as cd
from . import weapon_slots as ws

INCH = 0.0254


def _to_blender(v):
    """Source axes (X forward, Y left, Z up, inches) -> Source2Viewer glTF import axes in metres."""
    v = np.asarray(v, np.float64) * INCH
    return np.stack([v[:, 1], -v[:, 0], v[:, 2]], 1)


def _material(path):
    """A new material for each imported model: the stickers go into the weapon's material, so two
    imported weapons must not share one (Blender adds .001 etc. to repeated names)."""
    name = os.path.splitext(path.rsplit("/", 1)[-1])[0] or "material"
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except AttributeError:
        pass
    bsdf = next((n for n in mat.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled'), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.55, 0.55, 0.55, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.5
    mat["cs2_source"] = path
    return mat


def import_weapon(context, key, legacy, name=None):
    """Create the weapon's CS2 or legacy mesh as a new object at the 3D cursor and return it."""
    from .browser import cs2_root
    game = cd.Game.open(cs2_root(context))
    path = cd.weapon_model_path(game, ws.folder(key))
    if not path:
        raise RuntimeError("no model found for the %s" % ws.display(key))
    m = cd.read_model_mesh(game.read(path), "body_legacy" if legacy else "body_hd")

    pos = _to_blender(m["positions"])
    tris = m["triangles"]
    me = bpy.data.meshes.new(name or ws.display(key))
    me.vertices.add(len(pos))
    me.vertices.foreach_set("co", pos.astype(np.float32).ravel())
    me.loops.add(tris.size)
    me.loops.foreach_set("vertex_index", tris.astype(np.int32).ravel())
    me.polygons.add(len(tris))
    me.polygons.foreach_set("loop_start", np.arange(0, tris.size, 3, dtype=np.int32))
    me.polygons.foreach_set("loop_total", np.full(len(tris), 3, np.int32))
    me.polygons.foreach_set("material_index", m["tri_material"].astype(np.int32))
    for i in (0, 1):
        uv = m["uv%d" % i]
        if uv is not None:
            layer = me.uv_layers.new(name="TEXCOORD_%d" % i)
            layer.data.foreach_set("uv", uv[tris.ravel()].astype(np.float32).ravel())
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    if m["normals"] is not None and len(me.vertices) == len(pos):
        try:
            me.normals_split_custom_set_from_vertices(_to_blender(m["normals"] / INCH).astype(np.float32))
        except Exception:
            pass   # fall back to Blender's own smooth normals

    for p in m["materials"]:
        me.materials.append(_material(p))
    obj = bpy.data.objects.new(me.name, me)
    obj.location = context.scene.cursor.location
    context.collection.objects.link(obj)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    # the weapon body (the material with the most triangles) is where the stickers go
    if len(m["materials"]) > 1:
        counts = np.bincount(m["tri_material"], minlength=len(m["materials"]))
        obj.active_material_index = int(np.argmax(counts))
    obj["cs2_weapon"] = key
    obj["cs2_legacy_model"] = bool(legacy)
    return obj
