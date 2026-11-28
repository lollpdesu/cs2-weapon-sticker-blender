"""Read CS2 game data: VPK archive, compiled resources (.vmat_c / .vtex_c) and items_game stickers.

Uses a small subset of SourceIO (MIT, see sourceio/LICENSE) for the VPK reader, BCn decoding,
LZ4/Zstd and the binary KeyValues3 reader. File layouts follow ValveResourceFormat (MIT).
"""

import os
import struct

import numpy as np

from .sourceio.pylib import VPKFile
from .sourceio.pylib.compression import lz4_decompress, zstd_decompress
from .sourceio.pylib.image import decode_texture
from .sourceio.file_utils import MemoryBuffer
from .sourceio.keyvalues3.binary_keyvalues import read_valve_keyvalue3


# ====================================================================== game / VPK

GAME_SUBDIR = os.path.join("game", "csgo")
VPK_NAME = "pak01_dir.vpk"


def vpk_path_for(cs2_root):
    """cs2_root may be the install folder, game/, or game/csgo/."""
    if not cs2_root:
        return None
    root = os.path.normpath(bpy_abspath(cs2_root))
    for cand in (os.path.join(root, GAME_SUBDIR, VPK_NAME),
                 os.path.join(root, "csgo", VPK_NAME),
                 os.path.join(root, VPK_NAME)):
        if os.path.isfile(cand):
            return cand
    return None


def bpy_abspath(p):
    try:
        import bpy
        return bpy.path.abspath(p)
    except Exception:
        return p


def find_cs2_install():
    """Locate CS2 through Steam's libraryfolders.vdf. Returns the install folder or ''."""
    roots = []
    if os.name == "nt":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for val in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(k, val)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    else:
        home = os.path.expanduser("~")
        roots += [os.path.join(home, ".steam", "steam"), os.path.join(home, ".local", "share", "Steam"),
                  os.path.join(home, "Library", "Application Support", "Steam")]

    libraries = []
    for r in roots:
        r = os.path.normpath(r)
        if r not in libraries and os.path.isdir(r):
            libraries.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        parts = line.strip().split('"')
                        if len(parts) >= 4 and parts[1] == "path":
                            p = os.path.normpath(parts[3].replace("\\\\", "\\"))
                            if p not in libraries:
                                libraries.append(p)
            except OSError:
                pass
    for lib in libraries:
        cand = os.path.join(lib, "steamapps", "common", "Counter-Strike Global Offensive")
        if vpk_path_for(cand):
            return cand
    return ""


class Game:
    """Read-only access to the files inside pak01_dir.vpk."""

    _cache = {}

    def __init__(self, vpk_file):
        self.vpk_file = vpk_file
        self.vpk = VPKFile(vpk_file)

    @classmethod
    def open(cls, cs2_root):
        vpk_file = vpk_path_for(cs2_root)
        if vpk_file is None:
            raise FileNotFoundError("pak01_dir.vpk not found. Set the Counter-Strike 2 folder in the add-on preferences.")
        st = os.stat(vpk_file)
        stamp = (st.st_size, int(st.st_mtime))
        cached = cls._cache.get(vpk_file)
        if cached is None or cached[0] != stamp:
            # first use, or CS2 was updated: reopen so the new directory tree is read
            cls._cache[vpk_file] = (stamp, cls(vpk_file))
        return cls._cache[vpk_file][1]

    def exists(self, path):
        return bool(self.vpk.check(path.replace("\\", "/").lower()))

    def read(self, path):
        data = self.vpk.find_file(path.replace("\\", "/").lower())
        return bytes(data) if data else None


# ====================================================================== resource container

def resource_block_list(data):
    """[(block name, absolute offset, size)] of a compiled Source 2 resource, in file order
    (models hold several blocks with the same name, e.g. one MVTX/MIDX/MDAT set per mesh)."""
    if len(data) < 16:
        raise ValueError("file too small for a compiled resource")
    _file_size, header_version, _res_version = struct.unpack_from("<IHH", data, 0)
    if header_version != 12:
        raise ValueError(f"unexpected resource header version {header_version}")
    rel, count = struct.unpack_from("<II", data, 8)
    pos = 8 + rel
    out = []
    for i in range(count):
        p = pos + i * 12
        name = data[p:p + 4].decode("ascii", "replace")
        off, size = struct.unpack_from("<II", data, p + 4)
        out.append((name, p + 4 + off, size))
    return out


def resource_blocks(data):
    """Return {block name: (absolute offset, size)} of a compiled Source 2 resource (first of each name)."""
    blocks = {}
    for name, off, size in resource_block_list(data):
        blocks.setdefault(name, (off, size))
    return blocks


# ====================================================================== material (.vmat_c)

def _plain(v):
    """KV3 values -> plain python (lists, floats, ints, strings)."""
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.ndarray):
        return v.tolist()
    if hasattr(v, "tolist"):
        return v.tolist()
    return v


def read_material(data):
    """Compiled material -> dict(name, shader, ints, floats, vectors, textures)."""
    blocks = resource_blocks(data)
    if "DATA" not in blocks:
        raise ValueError("material has no DATA block")
    off, size = blocks["DATA"]
    kv = _plain(read_valve_keyvalue3(MemoryBuffer(data[off:off + size])))

    def pairs(key, value_key):
        out = {}
        for item in kv.get(key) or []:
            if isinstance(item, dict) and "m_name" in item:
                out[item["m_name"]] = item.get(value_key)
        return out

    return {
        "name": kv.get("m_materialName", ""),
        "shader": kv.get("m_shaderName", ""),
        "ints": pairs("m_intParams", "m_nValue"),
        "floats": pairs("m_floatParams", "m_flValue"),
        "vectors": pairs("m_vectorParams", "m_value"),
        "textures": pairs("m_textureParams", "m_pValue"),
    }


# ====================================================================== texture (.vtex_c)

FMT_DXT1, FMT_DXT5, FMT_I8, FMT_RGBA8888 = 1, 2, 3, 4
FMT_BC6H, FMT_BC7, FMT_ATI2N, FMT_ATI1N, FMT_BGRA8888 = 19, 20, 21, 27, 28

_BLOCK_FORMATS = {FMT_DXT1: 8, FMT_DXT5: 16, FMT_BC6H: 16, FMT_BC7: 16, FMT_ATI1N: 8, FMT_ATI2N: 16}
_PIXEL_FORMATS = {FMT_RGBA8888: 4, FMT_BGRA8888: 4, FMT_I8: 1}
_DECODER_NAME = {FMT_DXT1: "DXT1", FMT_DXT5: "DXT5", FMT_BC7: "BC7", FMT_ATI1N: "ATI1N", FMT_ATI2N: "ATI2N"}

EXTRA_COMPRESSED_MIP_SIZE = 4


def _mip_bytes(fmt, w, h, depth=1):
    if fmt in _BLOCK_FORMATS:
        bw, bh = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
        return (bw * bh // 16) * _BLOCK_FORMATS[fmt] * depth
    if fmt in _PIXEL_FORMATS:
        return w * h * _PIXEL_FORMATS[fmt] * depth
    raise NotImplementedError(f"unsupported texture format {fmt}")


def texture_info(data):
    blocks = resource_blocks(data)
    off, size = blocks["DATA"]
    (version, flags) = struct.unpack_from("<HH", data, off)
    if version != 1:
        raise NotImplementedError(f"texture data version {version}")
    w, h, depth = struct.unpack_from("<HHH", data, off + 20)
    fmt, mips = struct.unpack_from("<BB", data, off + 26)
    extra_rel, extra_count = struct.unpack_from("<II", data, off + 32)
    compression, mip_sizes = 0, None
    extra_pos = off + 32 + extra_rel
    for i in range(extra_count):
        p = extra_pos + i * 12
        etype, erel, esize = struct.unpack_from("<III", data, p)
        eabs = p + 4 + erel
        if etype == EXTRA_COMPRESSED_MIP_SIZE:
            compression, _unk, count = struct.unpack_from("<III", data, eabs)
            mip_sizes = list(struct.unpack_from(f"<{count}I", data, eabs + esize))
    return {
        "flags": flags, "width": w, "height": h, "depth": max(1, depth), "format": fmt, "mips": max(1, mips),
        "data_start": off + size, "compression": compression, "mip_sizes": mip_sizes,
    }


FMT_JPEG_RGBA8888, FMT_PNG_RGBA8888, FMT_JPEG_DXT5, FMT_PNG_DXT5 = 15, 16, 17, 18


def embedded_image(data):
    """For textures stored as an embedded PNG/JPEG file: (extension, file bytes). Otherwise None."""
    info = texture_info(data)
    fmt = info["format"]
    if fmt not in (FMT_JPEG_RGBA8888, FMT_PNG_RGBA8888, FMT_JPEG_DXT5, FMT_PNG_DXT5):
        return None
    blob = data[info["data_start"]:]
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", blob
    if blob[:2] == b"\xff\xd8":
        return ".jpg", blob
    # fall back to searching for the file signature
    for sig, ext in ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg")):
        i = data.find(sig, info["data_start"] - 64 if info["data_start"] > 64 else 0)
        if i >= 0:
            return ext, data[i:]
    return None


def _decode(fmt, raw, w, h):
    """Decoded RGBA uint8 array (h, w, 4), top row first."""
    if fmt in _DECODER_NAME:
        pw, ph = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
        out = decode_texture(raw, pw, ph, _DECODER_NAME[fmt])
        name = _DECODER_NAME[fmt]
        if name == "ATI1N":
            r = np.frombuffer(out, np.uint8).reshape(ph, pw)
            arr = np.empty((ph, pw, 4), np.uint8)
            arr[..., 0] = arr[..., 1] = arr[..., 2] = r
            arr[..., 3] = 255
        elif name == "ATI2N":
            rg = np.frombuffer(out, np.uint8).reshape(ph, pw, 2)
            arr = np.zeros((ph, pw, 4), np.uint8)
            arr[..., :2] = rg
            arr[..., 3] = 255
        else:
            arr = np.frombuffer(out, np.uint8).reshape(ph, pw, 4)
        return arr[:h, :w]
    if fmt == FMT_RGBA8888:
        return np.frombuffer(raw, np.uint8).reshape(h, w, 4)
    if fmt == FMT_BGRA8888:
        return np.frombuffer(raw, np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]]
    if fmt == FMT_I8:
        g = np.frombuffer(raw, np.uint8).reshape(h, w)
        arr = np.empty((h, w, 4), np.uint8)
        arr[..., 0] = arr[..., 1] = arr[..., 2] = g
        arr[..., 3] = 255
        return arr
    raise NotImplementedError(f"unsupported texture format {fmt}")


def read_texture(data, max_size=0):
    """Decode a compiled texture.

    max_size > 0 picks the largest mip whose longer side is <= max_size (for thumbnails).
    Returns (rgba uint8 array of shape (h, w, 4), top row first; info dict).
    No normal-map conversion is applied: channels come out exactly as the shader samples them.
    """
    info = texture_info(data)
    fmt, mips = info["format"], info["mips"]
    if info["flags"] & 0x10:
        raise NotImplementedError("cube textures are not supported")
    level = 0
    if max_size > 0:
        while level < mips - 1 and max(info["width"] >> level, info["height"] >> level) > max_size:
            level += 1

    def dims(l):
        return max(1, info["width"] >> l), max(1, info["height"] >> l)

    depth = info["depth"]
    pos = info["data_start"]
    sizes = info["mip_sizes"]
    compressed = info["compression"] in (1, 2) and sizes and len(sizes) >= mips
    # mips are stored smallest first: skip everything smaller than the wanted level
    for l in range(mips - 1, level, -1):
        w, h = dims(l)
        pos += sizes[l] if compressed else _mip_bytes(fmt, w, h, depth)
    w, h = dims(level)
    want = _mip_bytes(fmt, w, h, depth)
    if compressed:
        raw = data[pos:pos + sizes[level]]
        if len(raw) < want:
            raw = lz4_decompress(raw, want) if info["compression"] == 1 else zstd_decompress(raw, want)
    else:
        raw = data[pos:pos + want]
    if len(raw) < want:
        raise ValueError("texture data is truncated")
    arr = _decode(fmt, bytes(raw[:want]) if depth == 1 else bytes(raw[:want // depth]), w, h)
    info = dict(info, level=level, out_width=w, out_height=h)
    return arr, info


# ====================================================================== sticker index

INDEX_VERSION = 3

STICKER_TYPES = [  # (key, label) in display order
    ("PAPER", "Paper"), ("GLITTER", "Glitter"), ("HOLO", "Holo"), ("FOIL", "Foil"), ("GOLD", "Gold"),
    ("LENTICULAR", "Lenticular"), ("EMBROIDERED", "Embroidered"),
]
_SUFFIX_TYPE = {"holo": "HOLO", "foil": "FOIL", "gold": "GOLD", "glitter": "GLITTER",
                "lenticular": "LENTICULAR", "embroidered": "EMBROIDERED"}


def sticker_type(material):
    """Finish from the material name: a suffix (team_foil) or, for newer collections, a prefix (foil_name)."""
    base = material.rsplit("/", 1)[-1].lower()
    return _SUFFIX_TYPE.get(base.rsplit("_", 1)[-1]) or _SUFFIX_TYPE.get(base.split("_", 1)[0]) or "PAPER"


def thumbnail_path(material):
    return "panorama/images/econ/stickers/%s_png.vtex_c" % material


def material_path(material):
    return "stickers/%s.vmat_c" % material


# ====================================================================== weapon sticker slots (.vmdl_c)

def parse_kv3_text(text):
    """Minimal text KeyValues3 reader: objects, arrays, strings (incl. \"\"\"multi-line\"\"\"),
    numbers, true/false/null and bare identifiers. Comments and the <!-- header --> are skipped."""
    i, n = 0, len(text)

    def skip():
        nonlocal i
        while i < n:
            c = text[i]
            if c in " \t\r\n,":
                i += 1
            elif text.startswith("//", i):
                j = text.find("\n", i)
                i = n if j < 0 else j + 1
            elif text.startswith("/*", i):
                j = text.find("*/", i + 2)
                i = n if j < 0 else j + 2
            elif text.startswith("<!--", i):
                j = text.find("-->", i)
                i = n if j < 0 else j + 3
            else:
                return

    def word():
        nonlocal i
        j = i
        while j < n and text[j] not in " \t\r\n,=[]{}":
            j += 1
        w = text[i:j]
        i = j
        return w

    def value():
        nonlocal i
        skip()
        c = text[i]
        if c == "{":
            i += 1
            obj = {}
            while True:
                skip()
                if text[i] == "}":
                    i += 1
                    return obj
                key = string() if text[i] == '"' else word()
                skip()
                if text[i] == "=":
                    i += 1
                obj[key] = value()
        if c == "[":
            i += 1
            arr = []
            while True:
                skip()
                if text[i] == "]":
                    i += 1
                    return arr
                arr.append(value())
        if c == '"':
            return string()
        w = word()
        lw = w.lower()
        if lw == "true":
            return True
        if lw == "false":
            return False
        if lw == "null":
            return None
        try:
            return int(w)
        except ValueError:
            try:
                return float(w)
            except ValueError:
                return w

    def string():
        nonlocal i
        if text.startswith('"""', i):
            j = text.index('"""', i + 3)
            s = text[i + 3:j].strip("\r\n")
            i = j + 3
            return s
        j = i + 1
        out = []
        while text[j] != '"':
            if text[j] == "\\" and j + 1 < n:
                j += 1
            out.append(text[j])
            j += 1
        i = j + 1
        return "".join(out)

    return value()


def weapon_model_path(game, folder):
    """weapons/models/<folder>/weapon_*.vmdl_c (the weapon itself, not its magazine)."""
    hits = sorted(k for k, _d in game.vpk.glob("weapons/models/%s/weapon_*.vmdl_c" % folder)
                  if not k.endswith("_mag.vmdl_c"))
    return hits[0] if hits else None


def read_sticker_slots(data):
    """Every sticker slot in a compiled weapon model's StickerMarkup (model key values), for the
    CS2 (body_hd) and the legacy (body_legacy) mesh, including slots marked Deprecated.

    Returns a list of dicts: index (the game's slot number, 0-based), legacy, deprecated, x, y,
    scale, rotation (degrees), identifier; sorted CS2 first, then legacy, each by index.
    Offset/Scale are the same numbers the shader placement uses: the sticker center sits at UV
    (0.5 + x, 0.5 - y) of the weapon's second UV map and covers 1/scale of it. The rotation is
    stored in turns: csgo_weapon multiplies g_flStickerNRotation by 2*pi (e.g. the Glock's grip
    slot, 0.063 = 22.7 degrees, the grip angle)."""
    blocks = resource_blocks(data)
    off, size = blocks["DATA"]
    kv = _plain(read_valve_keyvalue3(MemoryBuffer(data[off:off + size])))
    text = (kv.get("m_modelInfo") or {}).get("m_keyValueText") or ""
    if "StickerMarkup" not in text:
        return []
    keys = parse_kv3_text(text)
    slots = []
    for n, s in enumerate(keys.get("StickerMarkup") or []):
        ox, oy = (list(s.get("Offset") or [0.0, 0.0]) + [0.0, 0.0])[:2]
        slots.append({
            "index": int(s.get("Index", n)),
            "legacy": bool(s.get("LegacyModel", False)),
            "deprecated": bool(s.get("Deprecated", False)),
            "x": float(ox), "y": float(oy),
            "scale": float(s.get("Scale", 1.0)),
            "rotation": float(s.get("Rotation", 0.0)) * 360.0,
            "identifier": str(s.get("SpecialIdentifier", "")),
        })
    slots.sort(key=lambda d: (d["legacy"], d["index"]))
    return slots


# ====================================================================== weapon meshes (.vmdl_c embedded meshes)

def _half2(raw, n, stride, off):
    return np.ndarray((n, 2), np.float16, raw, off, (stride, 2)).astype(np.float32)


def _decode_uv(raw, n, stride, off, fmt):
    if fmt == 34:      # DXGI R16G16_FLOAT
        return _half2(raw, n, stride, off)
    if fmt == 37:      # DXGI R16G16_SNORM
        return np.ndarray((n, 2), np.int16, raw, off, (stride, 2)).astype(np.float32) / 32767.0
    if fmt == 35:      # DXGI R16G16_UNORM
        return np.ndarray((n, 2), np.uint16, raw, off, (stride, 2)).astype(np.float32) / 65535.0
    if fmt == 16:      # DXGI R32G32_FLOAT
        return np.ndarray((n, 2), np.float32, raw, off, (stride, 4)).copy()
    raise ValueError("unsupported texture coordinate format %d" % fmt)


def _decode_tangent_frame(packed):
    """CS2 CompressedTangentFrame (R32_UINT): normal in 10+10 bit octahedral X/Y (bits 12..31)."""
    x = ((packed >> 12) & 0x3FF).astype(np.float32) / 1023.0 * 2.0 - 1.0
    y = ((packed >> 22) & 0x3FF).astype(np.float32) / 1023.0 * 2.0 - 1.0
    z = 1.0 - np.abs(x) - np.abs(y)
    comp = np.clip(-z, 0.0, 1.0)
    x = x + np.where(x >= 0, -comp, comp)
    y = y + np.where(y >= 0, -comp, comp)
    n = np.stack([x, y, z], 1)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-8)


def read_model_mesh(data, mesh_name):
    """One embedded mesh of a compiled model (e.g. a weapon's "body_hd" or "body_legacy").

    Returns dict(positions (N,3) in inches, Source axes; uv0, uv1 (N,2) with V pointing up as in
    Blender, or None; normals (N,3) or None; triangles (M,3); tri_material (M,) indices into
    materials [material paths]). Vertex/index buffers are meshopt-compressed (decoded by pylib)."""
    from .sourceio.pylib import mesh as pm
    blocks = resource_block_list(data)
    names = [b[0] for b in blocks]
    if "CTRL" not in names:
        raise ValueError("model has no embedded meshes")

    def kv(i):
        _n, o, s = blocks[i]
        return _plain(read_valve_keyvalue3(MemoryBuffer(data[o:o + s])))

    ctrl = kv(names.index("CTRL"))
    meshes = ctrl.get("embedded_meshes") or []
    m = next((e for e in meshes if e.get("m_Name") == mesh_name), None)
    if m is None:
        raise ValueError("mesh '%s' not found (model has: %s)" % (mesh_name, ", ".join(e.get("m_Name", "?") for e in meshes)))

    def buffer(desc, index=False):
        _n, o, s = blocks[desc["m_nBlockIndex"]]
        raw, size, count = data[o:o + s], desc["m_nElementSizeInBytes"], desc["m_nElementCount"]
        if desc.get("m_bCompressedZSTD"):
            raw = zstd_decompress(raw, size * count)
        if desc.get("m_bMeshoptCompressed"):
            raw = (pm.decode_index_buffer if index else pm.decode_vertex_buffer)(bytes(raw), size, count)
        return bytes(raw), size, count

    vb = m["m_vertexBuffers"][0]
    raw, stride, n = buffer(vb)
    out = {"positions": None, "uv0": None, "uv1": None, "normals": None}
    for f in vb["m_inputLayoutFields"]:
        sem, si, fmt, off = f["m_pSemanticName"], f["m_nSemanticIndex"], f["m_Format"], f["m_nOffset"]
        if sem == "POSITION" and fmt == 6:          # R32G32B32_FLOAT
            out["positions"] = np.ndarray((n, 3), np.float32, raw, off, (stride, 4)).copy()
        elif sem == "TEXCOORD" and si in (0, 1):
            uv = _decode_uv(raw, n, stride, off, fmt)
            uv[:, 1] = 1.0 - uv[:, 1]               # Source V points down
            out["uv%d" % si] = uv
        elif sem == "NORMAL" and fmt == 42 and si == 0:  # R32_UINT compressed tangent frame
            out["normals"] = _decode_tangent_frame(np.ndarray((n,), np.uint32, raw, off, (stride,)).copy())
    if out["positions"] is None:
        raise ValueError("mesh '%s' has no float positions" % mesh_name)

    ib = m["m_indexBuffers"][0]
    rawi, isize, _ic = buffer(ib, index=True)
    idx = np.frombuffer(rawi, np.uint16 if isize == 2 else np.uint32).astype(np.int64)
    mdat = kv(m["m_nDataBlock"])
    tris, mats, mat_names = [], [], []
    for so in mdat.get("m_sceneObjects") or []:
        for dc in so.get("m_drawCalls") or []:
            if dc.get("m_nPrimitiveType", "RENDER_PRIM_TRIANGLES") != "RENDER_PRIM_TRIANGLES":
                continue
            seg = idx[dc["m_nStartIndex"]:dc["m_nStartIndex"] + dc["m_nIndexCount"]] + dc.get("m_nBaseVertex", 0)
            mat = str(dc.get("m_material", "")).split(":", 1)[-1].strip('"')
            if mat not in mat_names:
                mat_names.append(mat)
            t = seg[:len(seg) // 3 * 3].reshape(-1, 3)
            tris.append(t)
            mats.append(np.full(len(t), mat_names.index(mat), np.int32))
    out["triangles"] = np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)
    out["tri_material"] = np.concatenate(mats) if mats else np.zeros(0, np.int32)
    out["materials"] = mat_names
    return out


def _pretty_folder(folder):
    import re
    s = re.sub(r"(\d+)", r" \1", folder.replace("_", " ")).strip()
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def build_index(game, progress=None):
    """Read items_game.txt + the English localization and list every applicable sticker.

    Returns {"version", "stickers": [[id, name, material, collection_key, type], ...],
             "collections": [[key, label], ...] (newest first),
             "weapons": {item id: "weapon_..."}, "legacy_paints": [paint kit ids using the legacy model],
             "paint_names": {paint kit id: English skin name}}.
    """
    def text_of(path):
        raw = game.read(path)
        if raw is None:
            return ""
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16", "replace")
        return raw.decode("utf-8-sig", "replace")

    items = parse_kv1(text_of("scripts/items/items_game.txt")).get("items_game", {})
    loc_root = parse_kv1(text_of("resource/csgo_english.txt"))
    lang = next(iter(loc_root.values()), {}) if loc_root else {}
    tokens = {k.lower(): v for k, v in (lang.get("Tokens") or lang.get("tokens") or {}).items()}

    def loc(token, fallback=""):
        # newer sticker kits store the localization key without the leading "#"
        if token:
            text = tokens.get(token.lstrip("#").lower())
            if text:
                return text
            if token.startswith("#"):
                return fallback
        return token or fallback

    def capsule_label(folder):
        """The game's name for a sticker capsule/collection folder, if it has one."""
        for t in ("csgo_crate_sticker_pack_%s_capsule", "coupon_%s_sticker_capsule", "csgo_crate_sticker_pack_%s_short",
                  "csgo_crate_sticker_pack_%s"):
            text = tokens.get(t % folder.lower())
            if text:
                return text
        return _pretty_folder(folder)

    stickers = []
    coll_label = {}
    coll_newest = {}
    kits = items.get("sticker_kits", {})
    total = len(kits)
    for i, (kid, kit) in enumerate(kits.items()):
        if progress and i % 500 == 0:
            progress(i, total)
        if not isinstance(kit, dict):
            continue
        mat = (kit.get("sticker_material") or "").strip().replace("\\", "/")
        if not mat:
            continue
        desc = (kit.get("description_string") or "").lower()
        if mat.endswith("_graffiti") or "graffiti" in desc or "spray" in desc:
            continue
        if not game.exists(material_path(mat)):
            continue
        try:
            sid = int(kid)
        except ValueError:
            continue
        name = loc(kit.get("item_name"), kit.get("name") or mat)
        parts = mat.split("/")
        folder = parts[0] if len(parts) > 1 else "misc"
        if folder == "community" and len(parts) > 2:
            folder = "community/" + parts[1]   # newer capsules: stickers/community/<capsule>/<name>
        ev = kit.get("tournament_event_id")
        if ev:
            key = "event_%s" % ev
            label = tokens.get("csgo_tournament_event_nameshort_%s" % ev) or tokens.get(
                "csgo_tournament_event_name_%s" % ev) or _pretty_folder(folder.rsplit("/", 1)[-1])
        else:
            key = "folder_%s" % folder
            label = capsule_label(folder.rsplit("/", 1)[-1])
        coll_label.setdefault(key, label)
        coll_newest[key] = max(coll_newest.get(key, 0), sid)
        stickers.append([sid, name, mat, key, sticker_type(mat)])

    stickers.sort(key=lambda s: -s[0])
    collections = sorted(coll_label.items(), key=lambda kv: -coll_newest[kv[0]])

    # for console (inspect) links: weapon item ids, skins that use the legacy model, skin names
    weapons = {}
    for did, item in (items.get("items") or {}).items():
        if isinstance(item, dict) and str(item.get("name", "")).startswith("weapon_") and did.isdigit():
            weapons[did] = item["name"]
    legacy_paints, paint_names = [], {}
    for pid, kit in (items.get("paint_kits") or {}).items():
        if not isinstance(kit, dict) or not pid.isdigit():
            continue
        if str(kit.get("use_legacy_model", "0")) == "1":
            legacy_paints.append(int(pid))
        name = loc(kit.get("description_tag"), "")
        if name:
            paint_names[pid] = name
    return {"version": INDEX_VERSION, "stickers": stickers, "collections": [list(c) for c in collections],
            "weapons": weapons, "legacy_paints": sorted(legacy_paints), "paint_names": paint_names}


# ====================================================================== KeyValues 1 (items_game.txt)

_KV_TOKEN = None


def _merge(dst, key, val):
    """Duplicate keys keep the first value; nested blocks with the same key are merged
    (items_game.txt splits sections such as sticker_kits across several blocks)."""
    old = dst.get(key)
    if old is None:
        dst[key] = val
    elif isinstance(old, dict) and isinstance(val, dict):
        for k, v in val.items():
            _merge(old, k, v)


def parse_kv1(text):
    """Small, forgiving KeyValues1 parser (keys are kept as written)."""
    import re
    global _KV_TOKEN
    if _KV_TOKEN is None:
        _KV_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*|\[[^\]\n]*\]|([^\s{}"]+)')
    root = {}
    stack = [root]
    key = None
    for m in _KV_TOKEN.finditer(text):
        quoted, brace, bare = m.group(1), m.group(2), m.group(3)
        if brace == "{":
            block = {}
            if key is not None:
                _merge(stack[-1], key, block)
                block = stack[-1][key] if isinstance(stack[-1].get(key), dict) else block
            stack.append(block)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif quoted is not None or bare is not None:
            tok = quoted if quoted is not None else bare
            if quoted is not None and "\\" in tok:
                tok = tok.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
            if key is None:
                key = tok
            else:
                _merge(stack[-1], key, tok)
                key = None
        # comments and [$PLATFORM] conditionals are skipped
    return root
