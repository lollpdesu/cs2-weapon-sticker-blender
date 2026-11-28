"""CS2 sticker browser: pick a sticker from the game files and import it with the sticker shader."""

import functools
import json
import os
import time

import bpy
import numpy as np
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)

from . import cs2data as cd
from . import nodes_build as nb
from . import weapon_slots as ws

ADDON_ID = __package__
PANEL_CATEGORY = "CS2 Sticker"


# ====================================================================== preferences

def prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


class CS2STICKER_OT_detect_cs2(bpy.types.Operator):
    bl_idname = "cs2sticker.detect_cs2"
    bl_label = "Find CS2 Automatically"
    bl_description = "Look for Counter-Strike 2 in your Steam libraries"

    def execute(self, context):
        path = cd.find_cs2_install()
        if not path:
            self.report({'WARNING'}, "Counter-Strike 2 was not found in your Steam libraries. Set the folder manually.")
            return {'CANCELLED'}
        prefs(context).cs2_path = path
        self.report({'INFO'}, "Found CS2: " + path)
        return {'FINISHED'}


class CS2STICKER_OT_uninstall(bpy.types.Operator):
    bl_idname = "cs2sticker.uninstall"
    bl_label = "Uninstall CS2 Weapon Sticker Shader"
    bl_description = ("Remove this add-on from Blender. Your .blend files and the sticker materials in them "
                      "are not touched")

    remove_cache: BoolProperty(
        name="Also delete the cached sticker list and textures", default=True,
        description="Delete the add-on's cache folder (sticker list, thumbnails, decoded textures)",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380, title="Uninstall CS2 Weapon Sticker Shader?",
                                                          confirm_text="Uninstall")

    def draw(self, context):
        col = self.layout.column()
        col.label(text="The add-on will be disabled and removed.")
        col.label(text="Your .blend files and their sticker materials stay as they are.")
        col.prop(self, "remove_cache")

    def execute(self, context):
        # Uninstalling unregisters this operator class. Doing it inside execute() would leave
        # Blender running a freed operator, so it runs from a timer after this operator has finished.
        bpy.app.timers.register(functools.partial(_uninstall_now, bool(self.remove_cache)), first_interval=0.1)
        self.report({'INFO'}, "Uninstalling CS2 Weapon Sticker Shader…")
        return {'FINISHED'}


def _uninstall_now(remove_cache):
    """Remove the add-on (runs from a timer, outside any operator of this add-on)."""
    import shutil
    addon_id = ADDON_ID
    if remove_cache:
        try:
            shutil.rmtree(bpy.utils.extension_path_user(addon_id), ignore_errors=True)
        except Exception:
            pass
    parts = addon_id.split(".")
    try:
        if len(parts) == 3 and parts[0] == "bl_ext":
            repo_module, pkg_id = parts[1], parts[2]
            repos = list(bpy.context.preferences.extensions.repos)
            index = next((i for i, r in enumerate(repos) if r.module == repo_module), None)
            if index is None:
                raise RuntimeError("extension repository '%s' not found" % repo_module)
            bpy.ops.extensions.package_uninstall(repo_index=index, pkg_id=pkg_id)
        else:  # installed as a legacy add-on
            bpy.ops.preferences.addon_disable(module=addon_id)
            bpy.ops.preferences.addon_remove(module=addon_id)
        print("CS2 Weapon Sticker Shader was uninstalled. Restart Blender to finish removing its files.")
    except Exception as e:
        print("CS2 Weapon Sticker Shader: uninstall failed:", e)
    return None


class CS2STICKER_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    cs2_path: StringProperty(
        name="Counter-Strike 2 Folder", subtype='DIR_PATH',
        description="The CS2 install folder (the one that contains game/csgo/pak01_dir.vpk)",
    )
    auto_update: BoolProperty(
        name="Update Sticker List Automatically", default=True,
        description="Load the sticker list when Blender starts and rebuild it when a CS2 update changes the game files "
                    "(checked once a minute)",
    )

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.prop(self, "cs2_path")
        row.operator(CS2STICKER_OT_detect_cs2.bl_idname, text="", icon='VIEWZOOM')
        if self.cs2_path:
            ok = cd.vpk_path_for(self.cs2_path) is not None
            layout.label(text="pak01_dir.vpk found" if ok else "pak01_dir.vpk not found in this folder",
                         icon='CHECKMARK' if ok else 'ERROR')
        layout.prop(self, "auto_update")
        layout.separator()
        row = layout.row()
        row.alert = True
        row.operator(CS2STICKER_OT_uninstall.bl_idname, text="Uninstall Add-on", icon='TRASH')


def cs2_root(context=None):
    p = prefs(context)
    if p is None:
        return ""
    if not p.cs2_path:
        found = cd.find_cs2_install()
        if found:
            p.cs2_path = found
    return p.cs2_path


# ====================================================================== index (cached)

_INDEX = {"data": None, "key": None, "error": "", "new_ids": set(), "updated": 0.0}
_COLLECTION_ITEMS = [("ALL", "All Collections", "")]
NEW_COLLECTION = "NEW"


def _cache_dir():
    try:
        return bpy.utils.extension_path_user(ADDON_ID, path="cache", create=True)
    except Exception:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "cs2_sticker_cache")
        os.makedirs(d, exist_ok=True)
        return d


def _index_key(vpk_file):
    st = os.stat(vpk_file)
    return "%d_%d_%d" % (cd.INDEX_VERSION, st.st_size, int(st.st_mtime))


def _set_index(data, key, new_ids=(), updated=0.0):
    global _COLLECTION_ITEMS
    _INDEX["data"], _INDEX["key"], _INDEX["error"] = data, key, ""
    _INDEX["new_ids"], _INDEX["updated"] = set(new_ids), updated
    items = [("ALL", "All Collections", "")]
    if _INDEX["new_ids"]:
        items.append((NEW_COLLECTION, "New in Latest CS2 Update (%d)" % len(_INDEX["new_ids"]),
                      "Stickers added by the most recent CS2 update"))
    _COLLECTION_ITEMS = items + [(k, label, "") for k, label in data["collections"]]


def _progress(context):
    """Progress bar callbacks; silently unavailable when there is no window (timers, background)."""
    wm = getattr(context, "window_manager", None)
    try:
        wm.progress_begin(0, 100)
    except Exception:
        return (lambda i, n: None), (lambda: None)

    def update(i, n):
        try:
            wm.progress_update(int(100 * i / max(1, n)))
        except Exception:
            pass

    def end():
        try:
            wm.progress_end()
        except Exception:
            pass
    return update, end


def load_index(context, rebuild=False):
    """Return the sticker index, building and caching it if needed. Raises on errors.

    The cache is keyed by the size and date of pak01_dir.vpk, so a CS2 update triggers a rebuild.
    Stickers that were not in the previous list are remembered as "new" until the next update.
    """
    root = cs2_root(context)
    vpk_file = cd.vpk_path_for(root)
    if vpk_file is None:
        raise FileNotFoundError("Counter-Strike 2 not found. Set its folder in the add-on preferences.")
    key = _index_key(vpk_file)
    if not rebuild and _INDEX["data"] is not None and _INDEX["key"] == key:
        return _INDEX["data"]
    cache_file = os.path.join(_cache_dir(), "sticker_index.json")
    cached = None
    if os.path.isfile(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                cached = json.load(f)
            cached["index"]["stickers"]  # validate
        except (OSError, ValueError, KeyError, TypeError):
            cached = None
    same_game = cached is not None and cached.get("key") == key
    # same game files, only the add-on's list format changed: rebuild, but keep the "new" marking
    game_part = key.split("_", 1)[-1]
    same_files = cached is not None and str(cached.get("key", "")).split("_", 1)[-1] == game_part
    if same_game and not rebuild:
        _set_index(cached["index"], key, cached.get("new_ids", []), cached.get("updated", 0.0))
        return _INDEX["data"]

    game = cd.Game.open(root)
    update, end = _progress(context)
    try:
        data = cd.build_index(game, progress=update)
    finally:
        end()

    if same_game or same_files:  # rebuild of the same game version: keep what was new
        new_ids, updated = cached.get("new_ids", []), cached.get("updated", 0.0)
    elif cached is not None:
        old = {s[0] for s in cached["index"]["stickers"]}
        new_ids = sorted(s[0] for s in data["stickers"] if s[0] not in old)
        updated = time.time() if new_ids else cached.get("updated", 0.0)
        if not new_ids:  # update without new stickers: keep the previous "new" set
            new_ids = [i for i in cached.get("new_ids", []) if any(s[0] == i for s in data["stickers"])]
    else:  # first run: nothing counts as new
        new_ids, updated = [], 0.0
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"key": key, "index": data, "new_ids": new_ids, "updated": updated}, f)
    except OSError:
        pass
    if not same_game and not same_files and _PREVIEWS["coll"] is not None:
        _PREVIEWS["coll"].clear()  # previews may have changed with the update
    _set_index(data, key, new_ids, updated)
    return data


def check_for_cs2_update(context=None):
    """Load the index if needed, rebuild it if the game files changed. Returns True if something changed."""
    context = context or bpy.context
    p = prefs(context)
    if p is None or not p.cs2_path:
        return False
    vpk_file = cd.vpk_path_for(p.cs2_path)
    if vpk_file is None:
        return False
    key = _index_key(vpk_file)
    if _INDEX["data"] is not None and _INDEX["key"] == key:
        return False
    had = _INDEX["data"] is not None
    before = len(_INDEX["new_ids"])
    load_index(context)
    if had or len(_INDEX["new_ids"]) != before:
        print("CS2 sticker: sticker list updated (%d stickers, %d new)" % (
            len(_INDEX["data"]["stickers"]), len(_INDEX["new_ids"])))
    return True


def index_ready():
    return _INDEX["data"] is not None


# ====================================================================== thumbnails

_PREVIEWS = {"coll": None}


def _previews():
    if _PREVIEWS["coll"] is None:
        from bpy.utils import previews
        _PREVIEWS["coll"] = previews.new()
    return _PREVIEWS["coll"]


def thumbnail_icon(context, material):
    """icon_id of the sticker's in-game preview image (decoded on first use)."""
    pcoll = _previews()
    pv = pcoll.get(material)
    if pv is not None:
        return pv.icon_id
    try:
        game = cd.Game.open(cs2_root(context))
        data = game.read(cd.thumbnail_path(material))
        if data is None:
            raise FileNotFoundError(cd.thumbnail_path(material))
        embedded = cd.embedded_image(data)
        if embedded is not None:
            # older previews are stored as a PNG/JPEG file inside the resource
            ext, blob = embedded
            path = os.path.join(_cache_dir(), "thumbs", material.replace("/", "__") + ext)
            if not os.path.isfile(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(blob)
            pv = pcoll.load(material, path, 'IMAGE')
        else:
            pv = pcoll.new(material)
            arr, _info = cd.read_texture(data, max_size=256)
            h, w = arr.shape[:2]
            px = np.flipud(arr).astype(np.float32) / 255.0
            px[..., :3] *= px[..., 3:4]   # previews are premultiplied: transparent pixels must be black
            pv.image_size = (w, h)
            pv.image_pixels_float.foreach_set(px.ravel())
    except Exception as e:  # missing or unreadable preview: leave it blank
        print("CS2 sticker preview failed for %s: %s" % (material, e))
        pv = pcoll.get(material) or pcoll.new(material)
    return pv.icon_id


# ====================================================================== browser state

TYPE_ITEMS = [("ALL", "All Types", "")] + [(k, label, "") for k, label in cd.STICKER_TYPES]
TYPE_LABEL = dict(cd.STICKER_TYPES)


def _collection_items(self, context):
    return _COLLECTION_ITEMS


_WEAPON_ITEMS = [
    (key, disp, "Sticker slots and mask of the %s, read from the game files" % disp)
    for key, (disp, _folder) in sorted(ws.WEAPONS.items(), key=lambda kv: kv[1][0].lower())]


def _weapon_items(self, context):
    return _WEAPON_ITEMS


def weapon_for(state, obj=None):
    """Weapon key for the import (the one chosen under Weapon)."""
    return state.weapon if state.weapon in ws.WEAPONS else None


_SLOT_CACHE = {}


def weapon_slots(context, key):
    """Every sticker slot of the weapon's model in the game files, CS2 and legacy, including
    deprecated ones (cached per CS2 version). See cs2data.read_sticker_slots."""
    game = cd.Game.open(cs2_root(context))
    ck = (game.vpk_file, os.path.getmtime(game.vpk_file), key)
    if ck not in _SLOT_CACHE:
        path = cd.weapon_model_path(game, ws.folder(key))
        _SLOT_CACHE[ck] = cd.read_sticker_slots(game.read(path)) if path else []
    return _SLOT_CACHE[ck]


def model_slots(context, key, legacy):
    """The slots the game offers on the CS2 or the legacy model (deprecated ones left out), in the
    add-on's slot order (weapon_slots.SLOT_ORDER, best position first). The add-on numbers them 1..N."""
    live = {s["index"]: s for s in weapon_slots(context, key) if s["legacy"] == bool(legacy) and not s["deprecated"]}
    order = ws.SLOT_ORDER.get(key, {}).get("legacy" if legacy else "hd", [])
    ranked = [live[i] for i in order if i in live]
    return ranked + [s for i, s in sorted(live.items()) if i not in order]


def find_slot(context, key, ident, legacy):
    """(slot number, slot entry) for the Slot enum value, or (0, None)."""
    slots = model_slots(context, key, legacy)
    try:
        n = int(ident)
    except (TypeError, ValueError):
        return 0, None
    return (n, slots[n - 1]) if 1 <= n <= len(slots) else (0, None)


_SLOT_ITEMS = {}   # Blender needs the enum item strings kept alive


def _slot_items(self, context):
    key = weapon_for(self)
    if key is None or context is None:
        return [("NONE", "No Slots", "")]
    legacy = self.model == 'LEGACY'
    try:
        slots = model_slots(context, key, legacy)
    except Exception:
        slots = []
    items = [(str(n), "Slot %d" % n,
              "X %.4f  Y %.4f  Scale %.3f  Rotation %.1f°" % (s["x"], s["y"], s["scale"], s["rotation"]),
              n)   # value = slot number, so switching CS2 <-> Legacy keeps it
             for n, s in enumerate(slots, 1)] or [("NONE", "No Slots", "")]
    _SLOT_ITEMS[(key, legacy)] = items
    return items


def sticker_uv_name(state, obj):
    """UV map for sticker placement: the chosen one, else the second, else the first."""
    layers = obj.data.uv_layers
    if state.uv_map and state.uv_map in layers:
        return state.uv_map
    if len(layers) >= 2:
        return layers[1].name
    return layers[0].name if len(layers) else ""


def _model_changed(self, context):
    """Switching CS2 <-> Legacy keeps the slot number if the other model has that many slots;
    otherwise the first slot is chosen."""
    items = [i[0] for i in _slot_items(self, context)]
    if self.slot not in items:
        self.slot = items[0]


def _reset_page(self, context):
    self.page = 0


class CS2StickerBrowserState(bpy.types.PropertyGroup):
    search: StringProperty(name="Search", description="Filter by name, team or tournament (all words must match)",
                           options={'TEXTEDIT_UPDATE'}, update=_reset_page)
    collection: EnumProperty(name="Collection", items=_collection_items, update=_reset_page,
                             description="Tournament or sticker collection")
    sticker_type: EnumProperty(name="Type", items=TYPE_ITEMS, update=_reset_page,
                               description="Sticker finish")
    page: IntProperty(name="Page", min=0, default=0)
    columns: IntProperty(name="Columns", min=1, max=8, default=2, update=_reset_page)
    rows: IntProperty(name="Rows", min=1, max=20, default=4, update=_reset_page)
    selected: IntProperty(name="Selected Sticker", default=-1)

    target: EnumProperty(
        name="Import To",
        items=[('PLANE', "New Plane", "Create a plane with the sticker's aspect ratio"),
               ('SELECTED', "Selected Objects", "Assign the sticker material to the active slot of the selected objects "
                                                "(legacy CS:GO sticker meshes, or your own UVs)"),
               ('WEAPON', "Weapon (CS2 Model)", "Layer the sticker into a sticker slot of the active weapon's material, "
                                                "placed on its UV map like CS2 models do"),
               ('LINK', "Console Link", "Place every sticker of a CS2 console (inspect) link on the weapon: "
                                        "slots, custom placement and wear, read from the link")],
        default='PLANE')
    link: StringProperty(name="Console Link",
                         description="Paste a console link from CS2 ('csgo_econ_action_preview 00…' with a long hex code). "
                                     "Read offline; old S…A…D… links contain no sticker data")
    link_model: EnumProperty(
        name="Weapon Model",
        items=[('SELECTED', "Selected Model", "Put the stickers on the weapon model selected in the viewport"),
               ('GAME', "Import from Game", "Import the weapon model from the game files (the CS2 or legacy model, "
                                            "whichever the skin uses) and put the stickers on it")],
        default='SELECTED')
    offset_x: FloatProperty(name="X", default=0.0, step=0.1, precision=4,
                            description="Custom placement: moves the sticker from the slot's position along the weapon's "
                                        "UV map (like offset X in a console link)")
    offset_y: FloatProperty(name="Y", default=0.0, step=0.1, precision=4,
                            description="Custom placement: moves the sticker from the slot's position, negative = "
                                        "towards the top of the UV map (like offset Y in a console link)")
    offset_rot: FloatProperty(name="R", default=0.0, min=-360.0, max=360.0, step=100, precision=1,
                              description="Custom placement: extra rotation in degrees, added to the slot's own "
                                          "rotation (like rotation in a console link)")
    plane_size: FloatProperty(name="Plane Size", default=1.0, min=0.001, soft_max=10.0, unit='LENGTH',
                              description="Length of the plane's longer side")
    weapon: EnumProperty(name="Weapon", items=_weapon_items,
                         description="Which weapon's sticker slot positions and sticker mask to use")
    model: EnumProperty(
        name="Model",
        items=[('HD', "CS2", "The current CS2 weapon model (body_hd slots, *_sticker_mask_hd)"),
               ('LEGACY', "Legacy", "The legacy weapon model used by legacy finishes (body_legacy slots, "
                                    "*_sticker_mask_legacy)")],
        default='HD', update=_model_changed,
        description="Which version of the weapon model it is. Picks that model's sticker slots and sticker mask")
    slot: EnumProperty(name="Slot", items=_slot_items,
                       description="Sticker slot of the chosen model, numbered like in the game")
    uv_map: StringProperty(name="UV Map",
                           description="UV map the stickers are placed on. CS2 models use the second UV map "
                                       "(TEXCOORD_1). Empty: the second UV map, or the first if there is only one")
    wear: FloatProperty(name="Wear", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
                        description="Initial sticker wear (g_flSticker0Wear). Not stored in the material; set per item in-game")


def filtered(state):
    data = _INDEX["data"]
    if data is None:
        return []
    words = [w for w in state.search.lower().split() if w]
    coll = state.collection if state.collection else "ALL"
    stype = state.sticker_type
    labels = dict(data["collections"])
    new_ids = _INDEX["new_ids"]
    out = []
    for s in data["stickers"]:
        sid, name, mat, ckey, typ = s
        if coll == NEW_COLLECTION:
            if sid not in new_ids:
                continue
        elif coll != "ALL" and ckey != coll:
            continue
        if stype != "ALL" and typ != stype:
            continue
        if words:
            hay = ("%s %s %s" % (name, mat, labels.get(ckey, ""))).lower()
            if not all(w in hay for w in words):
                continue
        out.append(s)
    return out


def sticker_by_id(sid):
    data = _INDEX["data"]
    if data is None:
        return None
    for s in data["stickers"]:
        if s[0] == sid:
            return s
    return None


# ====================================================================== operators: browsing

class CS2STICKER_OT_load_index(bpy.types.Operator):
    bl_idname = "cs2sticker.load_index"
    bl_label = "Load Sticker List"
    bl_description = "Read the sticker list from the CS2 game files (cached after the first time)"

    rebuild: BoolProperty(default=False, options={'SKIP_SAVE'})

    def execute(self, context):
        t = time.time()
        try:
            data = load_index(context, rebuild=self.rebuild)
        except Exception as e:
            _INDEX["error"] = str(e)
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "%d stickers loaded (%.1f s)" % (len(data["stickers"]), time.time() - t))
        return {'FINISHED'}


class CS2STICKER_OT_show_new(bpy.types.Operator):
    bl_idname = "cs2sticker.show_new"
    bl_label = "Show New Stickers"
    bl_description = "Show only the stickers added by the latest CS2 update"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        st = context.window_manager.cs2_sticker_browser
        st.search = ""
        st.sticker_type = 'ALL'
        try:
            st.collection = NEW_COLLECTION
        except TypeError:
            pass
        st.page = 0
        return {'FINISHED'}


class CS2STICKER_OT_page(bpy.types.Operator):
    bl_idname = "cs2sticker.page"
    bl_label = "Change Page"
    bl_options = {'INTERNAL'}

    delta: IntProperty(default=1)

    def execute(self, context):
        st = context.window_manager.cs2_sticker_browser
        per = st.columns * st.rows
        pages = max(1, (len(filtered(st)) + per - 1) // per)
        st.page = max(0, min(pages - 1, st.page + self.delta))
        return {'FINISHED'}


class CS2STICKER_OT_select(bpy.types.Operator):
    bl_idname = "cs2sticker.select_sticker"
    bl_label = "Select Sticker"
    bl_options = {'INTERNAL'}

    sticker_id: IntProperty()

    @classmethod
    def description(cls, context, properties):
        s = sticker_by_id(properties.sticker_id)
        return s[1] if s else "Select this sticker"

    def execute(self, context):
        context.window_manager.cs2_sticker_browser.selected = self.sticker_id
        return {'FINISHED'}


# ====================================================================== import

def _image_name(vtex_path, suffix=""):
    base = os.path.basename(vtex_path).rsplit(".", 1)[0]
    name = "CS2 " + base + suffix
    if len(name) > 63:
        name = name[:63]
    return name


def _make_image(name, arr, colorspace):
    """Image from an (h, w, 4) or (h, w) uint8 array (top row first), stored as a PNG in the
    add-on cache and packed into the .blend. Reuses an identical existing image."""
    from .sourceio.pylib.image import encode_png
    from . import _set_colorspace
    h, w = arr.shape[:2]
    img = bpy.data.images.get(name)
    if img is not None and tuple(img.size) == (w, h) and img.get("cs2_source") == name and img.has_data:
        return img  # shared textures (scratches, default masks) are reused across stickers
    channels = 1 if arr.ndim == 2 else arr.shape[2]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    path = os.path.join(_cache_dir(), "textures", safe + ".png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(bytes(encode_png(np.ascontiguousarray(arr).tobytes(), w, h, channels)))
    img = bpy.data.images.load(path, check_existing=False)
    img.name = name
    _set_colorspace(img, colorspace)
    img.alpha_mode = 'CHANNEL_PACKED'
    img["cs2_source"] = name
    try:
        img.pack()
    except RuntimeError as e:
        print("CS2 sticker: could not pack %s: %s" % (name, e))
    return img


def _texture_array(data, name):
    """(h, w, 4) uint8 array + info for any compiled texture, including embedded PNG/JPEG ones."""
    embedded = cd.embedded_image(data)
    if embedded is None:
        return cd.read_texture(data)
    ext, blob = embedded
    path = os.path.join(_cache_dir(), "tmp_" + name.replace("/", "_").replace(" ", "_") + ext)
    with open(path, "wb") as f:
        f.write(blob)
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        px = np.empty(w * h * 4, np.float32)
        img.pixels.foreach_get(px)
        arr = np.flipud((px.reshape(h, w, 4) * 255.0 + 0.5).astype(np.uint8))
    finally:
        bpy.data.images.remove(img)
        try:
            os.remove(path)
        except OSError:
            pass
    return arr, {"width": w, "height": h}


# (vmat texture param, [(slot prop, channel or None, colorspace)])
TEXTURE_MAP = [
    ("g_tSticker0", [("tex_sticker", None, 'sRGB')]),
    ("g_tNormalRoughnessSticker0", [("tex_sticker_nr", None, 'Non-Color'),
                                    ("tex_sticker_rough", 2, 'Non-Color'),
                                    ("tex_self_illum", 3, 'Non-Color')]),
    ("g_tSfxMaskSticker0", [("tex_holo_mask", None, 'Non-Color'),
                            ("tex_glitter_mask", 3, 'Non-Color')]),
    ("g_tHoloSpectrumSticker0", [("tex_spectrum", None, 'sRGB')]),
    ("g_tGlitterNormalSticker0", [("tex_glitter_normal", None, 'Non-Color'),
                                  ("tex_glitter_normal_mask", 3, 'Non-Color')]),
    ("g_tStickerScratches", [("tex_scratches", None, 'Non-Color')]),
]


def _socket_for_params():
    """Map material parameter names (g_*) to (group input name, component index or None)."""
    out = {}
    for _panel, name, _st, _d, _lo, _hi, desc in nb.INPUTS:
        if not desc or not desc.startswith("g_"):
            continue
        var = desc.split()[0]
        comp = None
        if var.endswith(".x"):
            var, comp = var[:-2], 0
        elif var.endswith(".y"):
            var, comp = var[:-2], 1
        out.setdefault(var, []).append((name, comp))
    return out


def load_sticker(context, sticker):
    """Read a sticker's compiled material and decode its textures.

    Returns (vmat, images {slot prop: Image}, aspect ratio of the sticker texture)."""
    _sid, _display, mat_path, _coll, _typ = sticker
    game = cd.Game.open(cs2_root(context))
    raw = game.read(cd.material_path(mat_path))
    if raw is None:
        raise FileNotFoundError("Material not found: " + cd.material_path(mat_path))
    vmat = cd.read_material(raw)
    if vmat["shader"] and "csgo_weapon_sticker" not in vmat["shader"]:
        print("CS2 sticker import: %s uses shader %s" % (mat_path, vmat["shader"]))
    images, aspect = {}, 1.0
    for param, targets in TEXTURE_MAP:
        vtex = vmat["textures"].get(param)
        if not vtex:
            continue
        data = game.read(vtex if vtex.endswith("_c") else vtex + "_c")
        if data is None:
            print("CS2 sticker import: missing texture", vtex)
            continue
        arr, info = _texture_array(data, vtex)
        if param == "g_tSticker0":
            aspect = info["width"] / max(1, info["height"])
        for prop, channel, colorspace in targets:
            if channel is None:
                img = _make_image(_image_name(vtex), arr, colorspace)
            else:
                img = _make_image(_image_name(vtex, " %s" % "RGBA"[channel]),
                                  np.ascontiguousarray(arr[..., channel]), colorspace)
            images[prop] = img
    return vmat, images, aspect


def apply_material_values(node, vmat, images, wear):
    """Set the sticker group node's inputs from the compiled material (anything not set keeps the default)."""
    if images.get("tex_glitter_mask") is not None and "Invert Glitter Mask" in node.inputs:
        node.inputs["Invert Glitter Mask"].default_value = 1.0  # compiled alpha: black = glitter
    values = {}
    for group in ("ints", "floats", "vectors"):
        values.update(vmat[group])
    for var, sockets in _socket_for_params().items():
        if var not in values:
            continue
        val = values[var]
        for sock_name, comp in sockets:
            sock = node.inputs.get(sock_name)
            if sock is None:
                continue
            try:
                if sock.type == 'RGBA':
                    rgb = [nb_srgb_to_linear(float(c)) for c in list(val)[:3]]  # UI value is sRGB
                    sock.default_value = (*rgb, 1.0)
                elif comp is not None:
                    sock.default_value = float(list(val)[comp])
                elif isinstance(val, (list, tuple)):
                    sock.default_value = float(val[0])
                else:
                    sock.default_value = float(val)
            except (TypeError, ValueError, IndexError):
                pass
    if "Wear" in node.inputs:
        node.inputs["Wear"].default_value = wear


def import_sticker(context, sticker, state):
    """Create a material for `sticker` ([id, name, material, collection, type]) and return it."""
    from . import setup_material, refresh_wear_mask

    sid, display, mat_path, _coll, _typ = sticker
    vmat, images, aspect = load_sticker(context, sticker)
    mat = bpy.data.materials.new("CS2 Sticker | %s" % display)
    try:
        mat.use_nodes = True
    except AttributeError:
        pass
    for n in list(mat.node_tree.nodes):
        if n.bl_idname != 'ShaderNodeOutputMaterial':
            mat.node_tree.nodes.remove(n)
    node = setup_material(mat, context.scene)
    mat["cs2_sticker_id"] = sid
    mat["cs2_sticker_material"] = mat_path
    s = mat.cs2_sticker
    for prop, img in images.items():
        setattr(s, prop, img)
    refresh_wear_mask(mat)
    apply_material_values(node, vmat, images, state.wear)
    return mat, aspect


def nb_srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _make_plane(context, name, aspect, size):
    import bmesh
    mesh = bpy.data.meshes.new(name)
    w, h = (size, size / aspect) if aspect >= 1.0 else (size * aspect, size)
    bm = bmesh.new()
    verts = [bm.verts.new((x * w / 2, y * h / 2, 0.0)) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    face = bm.faces.new(verts)
    uv = bm.loops.layers.uv.new("UVMap")
    for loop, co in zip(face.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop[uv].uv = co
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    obj.location = context.scene.cursor.location
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj




def load_weapon_mask(context, key, legacy=False):
    """The weapon's sticker mask (red channel of weapons/models/<weapon>/materials/stickers/*_sticker_mask_hd*,
    or *_sticker_mask_legacy* for the legacy model) as a packed grayscale image, or None if the weapon has none."""
    game = cd.Game.open(cs2_root(context))
    folder = ws.folder(key)
    kind = "legacy" if legacy else "hd"
    hits = sorted(k for k, _data in game.vpk.glob("weapons/models/%s/materials/stickers/*sticker_mask_%s*" % (folder, kind)))
    if not hits:
        return None
    arr, _info = cd.read_texture(game.read(hits[0]))
    return _make_image(_image_name(hits[0], " R"), np.ascontiguousarray(arr[..., 0]), 'Non-Color')


class CS2STICKER_OT_import(bpy.types.Operator):
    bl_idname = "cs2sticker.import_sticker"
    bl_label = "Import Sticker"
    bl_description = "Import the selected sticker with its textures and in-game material settings"
    bl_options = {'REGISTER', 'UNDO'}

    sticker_id: IntProperty(default=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return index_ready()

    def execute(self, context):
        st = context.window_manager.cs2_sticker_browser
        if st.target == 'LINK':
            return self.import_link(context, st)
        sid = self.sticker_id if self.sticker_id >= 0 else st.selected
        sticker = sticker_by_id(sid)
        if sticker is None:
            self.report({'WARNING'}, "Select a sticker first")
            return {'CANCELLED'}
        if st.target == 'SELECTED' and not [o for o in context.selected_objects if hasattr(o.data, "materials")]:
            self.report({'WARNING'}, "Select one or more mesh objects, or import to a new plane")
            return {'CANCELLED'}
        weapon_obj = key = uv_name = None
        if st.target == 'WEAPON':
            weapon_obj = context.active_object
            if weapon_obj is None or weapon_obj.type != 'MESH':
                self.report({'WARNING'}, "Select the weapon (a mesh object) first")
                return {'CANCELLED'}
            key = weapon_for(st)
            if key is None:
                self.report({'WARNING'}, "Choose the weapon under Weapon")
                return {'CANCELLED'}
            try:
                slot_no, slot_data = find_slot(context, key, st.slot, st.model == 'LEGACY')
            except Exception as e:
                self.report({'ERROR'}, "Could not read the %s's sticker slots: %s" % (ws.display(key), e))
                return {'CANCELLED'}
            if slot_data is None:
                self.report({'WARNING'}, "Choose a sticker slot of the %s" % ws.display(key))
                return {'CANCELLED'}
            uv_name = sticker_uv_name(st, weapon_obj)
            if not uv_name:
                self.report({'WARNING'}, "'%s' has no UV map" % weapon_obj.name)
                return {'CANCELLED'}
            slot_data = with_offsets(slot_data, st.offset_x, st.offset_y, st.offset_rot)
        t = time.time()
        if st.target == 'WEAPON':
            from . import weapon_layer
            try:
                mat, _node, mask = weapon_layer.place_sticker(context, weapon_obj, key, slot_no, slot_data,
                                                              uv_name, sticker, st)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({'ERROR'}, "Import failed: %s" % e)
                return {'CANCELLED'}
            self.report({'INFO'}, "%s: %s of %s in material '%s'%s (%.1f s)" % (
                sticker[1], "Slot %d" % slot_no, ws.display(key), mat.name,
                "" if mask else ", no %ssticker mask found for this weapon" % ("legacy " if st.model == 'LEGACY' else ""),
                time.time() - t))
            return {'FINISHED'}
        try:
            mat, aspect = import_sticker(context, sticker, st)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, "Import failed: %s" % e)
            return {'CANCELLED'}
        if st.target == 'PLANE':
            obj = _make_plane(context, sticker[1], aspect, st.plane_size)
            obj.data.materials.append(mat)
        else:
            for o in context.selected_objects:
                if not hasattr(o.data, "materials"):
                    continue
                if len(o.material_slots) == 0:
                    o.data.materials.append(mat)
                else:
                    o.material_slots[o.active_material_index].material = mat
        self.report({'INFO'}, "Imported %s (%.1f s)" % (sticker[1], time.time() - t))
        return {'FINISHED'}


    def import_link(self, context, st):
        from . import inspect_link, weapon_layer, weapon_model
        try:
            item = inspect_link.decode(st.link)
        except inspect_link.LinkError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        data = _INDEX["data"] or {}
        classname = (data.get("weapons") or {}).get(str(item["defindex"]))
        key = ws.key_for_classname(classname)
        if key is None:
            self.report({'WARNING'}, "The link is for %s, which doesn't take stickers here" % (
                classname or "item %d" % item["defindex"]))
            return {'CANCELLED'}
        legacy = item["paintindex"] in set(data.get("legacy_paints") or [])
        if not item["stickers"]:
            self.report({'WARNING'}, "The link's %s has no stickers" % ws.display(key))
            return {'CANCELLED'}
        t = time.time()
        if st.link_model == 'GAME':
            skin = (data.get("paint_names") or {}).get(str(item["paintindex"]))
            try:
                weapon_obj = weapon_model.import_weapon(context, key, legacy,
                                                        "%s | %s" % (ws.display(key), skin) if skin else None)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({'ERROR'}, "Could not import the %s model: %s" % (ws.display(key), e))
                return {'CANCELLED'}
        else:
            weapon_obj = context.active_object
            if weapon_obj is None or weapon_obj.type != 'MESH':
                self.report({'WARNING'}, "Select the weapon model first, or choose Import from Game")
                return {'CANCELLED'}
        uv_name = sticker_uv_name(st, weapon_obj)
        if not uv_name:
            self.report({'WARNING'}, "'%s' has no UV map" % weapon_obj.name)
            return {'CANCELLED'}
        # show what the link chose in the Weapon settings too
        st.weapon = key
        st.model = 'LEGACY' if legacy else 'HD'
        try:
            all_slots = weapon_slots(context, key)
            live = model_slots(context, key, legacy)
        except Exception as e:
            self.report({'ERROR'}, "Could not read the %s's sticker slots: %s" % (ws.display(key), e))
            return {'CANCELLED'}
        placed, missing, used = 0, [], set()
        for s in item["stickers"]:
            sticker = sticker_by_id(s["sticker_id"])
            entry = next((e for e in all_slots if e["index"] == s["slot"] and e["legacy"] == legacy), None)
            if sticker is None or entry is None:
                missing.append("sticker %d" % s["sticker_id"] if sticker is None else "slot %d" % s["slot"])
                continue
            shown = next((n for n, e in enumerate(live, 1) if e is entry), 100 + entry["index"])
            # with custom placement several stickers can share a game slot: each gets its own layer
            # (numbers 10 apart, so importing the same link again replaces the same layers)
            slot_no = shown
            while slot_no in used:
                slot_no += 10
            used.add(slot_no)
            data_slot = with_offsets(entry, s["offset_x"], s["offset_y"], s["rotation"])
            try:
                weapon_layer.place_sticker(context, weapon_obj, key, slot_no, data_slot, uv_name, sticker, st,
                                           wear=min(max(s["wear"], 0.0), 1.0), legacy=legacy,
                                           slot_label="Slot %d" % shown if shown < 100 else None)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({'ERROR'}, "Import failed: %s" % e)
                return {'CANCELLED'}
            placed += 1
        self.report({'WARNING'} if missing else {'INFO'}, "%d sticker%s on the %s (%s model)%s (%.1f s)" % (
            placed, "" if placed == 1 else "s", ws.display(key), "legacy" if legacy else "CS2",
            (", skipped: " + ", ".join(missing)) if missing else "", time.time() - t))
        return {'FINISHED'} if placed else {'CANCELLED'}


def with_offsets(slot_data, dx, dy, drot):
    """A copy of a slot entry moved and rotated by a custom placement (as CS2 applies it:
    offset added to the slot's position, rotation in degrees added to the slot's rotation)."""
    d = dict(slot_data)
    d["x"] = slot_data["x"] + dx
    d["y"] = slot_data["y"] + dy
    d["rotation"] = slot_data["rotation"] + drot
    return d


# ====================================================================== panel

class CS2STICKER_PT_browser(bpy.types.Panel):
    bl_label = "CS2 Sticker Browser"
    bl_idname = "CS2STICKER_PT_browser"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = PANEL_CATEGORY

    def draw(self, context):
        layout = self.layout
        st = context.window_manager.cs2_sticker_browser
        p = prefs(context)
        root = p.cs2_path if p else ""
        if not cd.vpk_path_for(root):
            col = layout.column()
            col.label(text="Counter-Strike 2 not found", icon='ERROR')
            if p is not None:
                row = col.row(align=True)
                row.prop(p, "cs2_path", text="")
                row.operator(CS2STICKER_OT_detect_cs2.bl_idname, text="", icon='VIEWZOOM')
            return
        if not index_ready():
            col = layout.column()
            col.operator(CS2STICKER_OT_load_index.bl_idname, icon='IMPORT')
            if _INDEX["error"]:
                col.label(text=_INDEX["error"], icon='ERROR')
            return

        if _INDEX["new_ids"] and _INDEX["updated"] and time.time() - _INDEX["updated"] < 14 * 86400:
            box = layout.box().column(align=True)
            box.label(text="%d new stickers" % len(_INDEX["new_ids"]), icon='INFO')
            box.label(text="from the latest CS2 update")
            box.operator(CS2STICKER_OT_show_new.bl_idname, text="Show New Stickers")

        col = layout.column(align=True)
        col.prop(st, "search", text="", icon='VIEWZOOM')
        row = col.row(align=True)
        row.prop(st, "collection", text="")
        row.prop(st, "sticker_type", text="")

        items = filtered(st)
        per = st.columns * st.rows
        pages = max(1, (len(items) + per - 1) // per)
        page = min(st.page, pages - 1)
        row = layout.row(align=True)
        row.operator(CS2STICKER_OT_page.bl_idname, text="", icon='TRIA_LEFT').delta = -1
        row.label(text="%d stickers  ·  page %d / %d" % (len(items), page + 1, pages))
        row.operator(CS2STICKER_OT_page.bl_idname, text="", icon='TRIA_RIGHT').delta = 1

        grid = layout.grid_flow(row_major=True, columns=st.columns, even_columns=True, even_rows=True, align=True)
        for sticker in items[page * per:(page + 1) * per]:
            sid, name, mat, _ckey, _typ = sticker
            cell = grid.box().column(align=True)
            cell.template_icon(icon_value=thumbnail_icon(context, mat), scale=6.0)
            op = cell.operator(CS2STICKER_OT_select.bl_idname, text=_short(name), depress=(sid == st.selected))
            op.sticker_id = sid

        sel = sticker_by_id(st.selected)
        box = layout.box()
        if sel is None:
            box.label(text="Click a sticker to select it", icon='INFO')
        else:
            sid, name, mat, ckey, typ = sel
            box.label(text=name, icon='CHECKMARK')
            sub = box.column(align=True)
            sub.scale_y = 0.8
            sub.label(text="%s  ·  %s" % (TYPE_LABEL.get(typ, typ), dict(_INDEX["data"]["collections"]).get(ckey, "")))
            sub.label(text=mat)
        col = box.column()
        col.use_property_split = True
        col.prop(st, "target")
        if st.target == 'LINK':
            col.prop(st, "link", text="Link")
            row = col.row(align=True)
            row.prop(st, "link_model", expand=True)
            ob = context.active_object
            if st.link_model == 'SELECTED' and ob is not None and ob.type == 'MESH':
                col.prop_search(st, "uv_map", ob.data, "uv_layers", text="UV Map")
            row = box.row()
            row.scale_y = 1.5
            row.enabled = bool(st.link.strip())
            row.operator(CS2STICKER_OT_import.bl_idname, text="Import from Link", icon='IMPORT')
        elif st.target == 'PLANE':
            col.prop(st, "plane_size")
        elif st.target == 'WEAPON':
            ob = context.active_object
            col.prop(st, "weapon")
            col.prop(st, "model")
            key = weapon_for(st)
            slot_err = None
            if key is not None:
                try:
                    _n, sel_slot = find_slot(context, key, st.slot, st.model == 'LEGACY')
                except Exception as e:
                    slot_err = str(e)
            col.prop(st, "slot")
            if ob is not None and ob.type == 'MESH':
                col.prop_search(st, "uv_map", ob.data, "uv_layers", text="UV Map")
            if slot_err:
                col.label(text="No sticker slots found in the game files", icon='ERROR')
        if st.target != 'LINK':
            col.prop(st, "wear", slider=True)
            if st.target == 'WEAPON':
                row = col.row(align=True)
                row.prop(st, "offset_x")
                row.prop(st, "offset_y")
                row.prop(st, "offset_rot")
            row = box.row()
            row.scale_y = 1.5
            row.enabled = sel is not None
            row.operator(CS2STICKER_OT_import.bl_idname, icon='IMPORT')

        opts = layout.row(align=True)
        opts.prop(st, "columns")
        opts.prop(st, "rows")
        opts.operator(CS2STICKER_OT_load_index.bl_idname, text="", icon='FILE_REFRESH').rebuild = True


def _short(name, n=22):
    name = name.split(" | ")[0]
    return name if len(name) <= n else name[:n - 1] + "…"


# ====================================================================== registration

classes = [
    CS2STICKER_OT_detect_cs2,
    CS2STICKER_OT_uninstall,
    CS2STICKER_Preferences,
    CS2StickerBrowserState,
    CS2STICKER_OT_load_index,
    CS2STICKER_OT_show_new,
    CS2STICKER_OT_page,
    CS2STICKER_OT_select,
    CS2STICKER_OT_import,
    CS2STICKER_PT_browser,
]


def _auto_detect():
    """Fill in the CS2 folder once if it is empty (runs from a timer, not while drawing)."""
    try:
        p = prefs()
        if p is not None and not p.cs2_path:
            found = cd.find_cs2_install()
            if found:
                p.cs2_path = found
    except Exception as e:
        print("CS2 sticker: auto-detect failed:", e)
    return None


WATCH_INTERVAL = 60.0


def _redraw_browser():
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass


def _watch():
    """Timer: load the sticker list after startup and rebuild it after a CS2 update."""
    try:
        p = prefs()
        if p is None:
            return None  # add-on no longer enabled
        if not p.auto_update:
            return WATCH_INTERVAL
        if not p.cs2_path:
            _auto_detect()
        if check_for_cs2_update():
            _redraw_browser()
    except Exception as e:
        # e.g. Steam is still writing the update: try again on the next tick
        _INDEX["error"] = str(e)
        print("CS2 sticker: update check failed:", e)
    return WATCH_INTERVAL


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.cs2_sticker_browser = PointerProperty(type=CS2StickerBrowserState)
    bpy.app.timers.register(_auto_detect, first_interval=0.5)
    bpy.app.timers.register(_watch, first_interval=2.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    del bpy.types.WindowManager.cs2_sticker_browser
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    if _PREVIEWS["coll"] is not None:
        from bpy.utils import previews
        previews.remove(_PREVIEWS["coll"])
        _PREVIEWS["coll"] = None
