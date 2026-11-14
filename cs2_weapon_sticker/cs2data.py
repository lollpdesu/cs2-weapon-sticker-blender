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

def resource_blocks(data):
    """Return {block name: (absolute offset, size)} of a compiled Source 2 resource."""
    if len(data) < 16:
        raise ValueError("file too small for a compiled resource")
    _file_size, header_version, _res_version = struct.unpack_from("<IHH", data, 0)
    if header_version != 12:
        raise ValueError(f"unexpected resource header version {header_version}")
    rel, count = struct.unpack_from("<II", data, 8)
    pos = 8 + rel
    blocks = {}
    for i in range(count):
        p = pos + i * 12
        name = data[p:p + 4].decode("ascii", "replace")
        off, size = struct.unpack_from("<II", data, p + 4)
        blocks.setdefault(name, (p + 4 + off, size))
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

INDEX_VERSION = 1

STICKER_TYPES = [  # (key, label) in display order
    ("PAPER", "Paper"), ("GLITTER", "Glitter"), ("HOLO", "Holo"), ("FOIL", "Foil"), ("GOLD", "Gold"),
    ("LENTICULAR", "Lenticular"), ("EMBROIDERED", "Embroidered"),
]
_SUFFIX_TYPE = {"holo": "HOLO", "foil": "FOIL", "gold": "GOLD", "glitter": "GLITTER",
                "lenticular": "LENTICULAR", "embroidered": "EMBROIDERED"}


def sticker_type(material):
    tail = material.rsplit("/", 1)[-1].rsplit("_", 1)[-1].lower()
    return _SUFFIX_TYPE.get(tail, "PAPER")


def thumbnail_path(material):
    return "panorama/images/econ/stickers/%s_png.vtex_c" % material


def material_path(material):
    return "stickers/%s.vmat_c" % material


def _pretty_folder(folder):
    import re
    s = re.sub(r"(\d+)", r" \1", folder.replace("_", " ")).strip()
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def build_index(game, progress=None):
    """Read items_game.txt + the English localization and list every applicable sticker.

    Returns {"version", "stickers": [[id, name, material, collection_key, type], ...],
             "collections": [[key, label], ...] (newest first)}.
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
        if token and token.startswith("#"):
            return tokens.get(token[1:].lower(), fallback) or fallback
        return token or fallback

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
        folder = mat.split("/", 1)[0] if "/" in mat else "misc"
        ev = kit.get("tournament_event_id")
        if ev:
            key = "event_%s" % ev
            label = tokens.get("csgo_tournament_event_nameshort_%s" % ev) or tokens.get(
                "csgo_tournament_event_name_%s" % ev) or _pretty_folder(folder)
        else:
            key = "folder_%s" % folder
            label = _pretty_folder(folder)
        coll_label.setdefault(key, label)
        coll_newest[key] = max(coll_newest.get(key, 0), sid)
        stickers.append([sid, name, mat, key, sticker_type(mat)])

    stickers.sort(key=lambda s: -s[0])
    collections = sorted(coll_label.items(), key=lambda kv: -coll_newest[kv[0]])
    return {"version": INDEX_VERSION, "stickers": stickers, "collections": [list(c) for c in collections]}


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
