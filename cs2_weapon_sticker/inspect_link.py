"""CS2 console (inspect) links: decode the item data they carry, fully offline.

A console link looks like

    csgo_econ_action_preview 00180920F405...        (in the game console)
    steam://rungame/730/.../+csgo_econ_action_preview%2000180920F405...

The hex string is: 1 key byte, the item (protobuf CEconItemPreviewDataBlock) XOR'ed with that key,
and a 4-byte checksum. Older links of the form S<steamid>A<assetid>D<number> only point at an
item on Valve's servers and carry no data, so they can't be read offline.
"""

import re
import struct
import zlib

# CEconItemPreviewDataBlock / Sticker field numbers
_ITEM = {3: "defindex", 4: "paintindex", 5: "rarity", 6: "quality", 7: "paintwear", 8: "paintseed",
         11: "customname"}
_STICKER = {1: "slot", 2: "sticker_id", 3: "wear", 4: "scale", 5: "rotation", 6: "tint_id",
            7: "offset_x", 8: "offset_y", 9: "offset_z", 10: "pattern"}


class LinkError(ValueError):
    pass


def _varint(d, i):
    v = shift = 0
    while True:
        if i >= len(d):
            raise LinkError("the link data is cut off")
        b = d[i]
        i += 1
        v |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return v, i


def _fields(d):
    """Protobuf wire format -> [(field number, value)]; fixed32 fields are read as floats."""
    i, out = 0, []
    while i < len(d):
        tag, i = _varint(d, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            v, i = _varint(d, i)
        elif wire == 5:
            v = struct.unpack_from("<f", d, i)[0]
            i += 4
        elif wire == 1:
            v = struct.unpack_from("<d", d, i)[0]
            i += 8
        elif wire == 2:
            n, i = _varint(d, i)
            v = d[i:i + n]
            i += n
        else:
            raise LinkError("unknown data in the link (wire type %d)" % wire)
        out.append((field, v))
    return out


def extract_hex(text):
    """The hex item data from anything the user pastes (console command, steam:// URL, bare hex)."""
    t = (text or "").strip().replace("%20", " ")
    m = re.search(r"csgo_econ_action_preview\s+([0-9A-Fa-f]+)\b", t)
    if m:
        return m.group(1)
    if re.fullmatch(r"[0-9A-Fa-f]{20,}", t):
        return t
    if re.search(r"csgo_econ_action_preview\s*[SM]\d+A\d+D\d+", t) or re.search(r"\b[SM]\d{5,}A\d+D\d+\b", t):
        raise LinkError("This is an old-style inspect link (S…A…D…). It only points at the item on Valve's "
                        "servers and contains no sticker data. Use a console link with a long hex code")
    raise LinkError("No console link found. Paste the whole 'csgo_econ_action_preview …' line")


def decode(text):
    """Decode a console link. Returns dict(defindex, paintindex, paintwear, paintseed, stickers=[...]).
    Each sticker: dict(slot, sticker_id, wear, rotation, offset_x, offset_y, scale); missing values are 0."""
    hx = extract_hex(text)
    if len(hx) % 2:
        raise LinkError("the link's hex code has an odd length (cut off?)")
    raw = bytes.fromhex(hx)
    if len(raw) < 6:
        raise LinkError("the link's hex code is too short")
    key = raw[0]
    body = bytes(b ^ key for b in raw)
    payload, crc = body[1:-4], body[-4:]
    c = zlib.crc32(body[:-4]) & 0xFFFFFFFF
    folded = ((c & 0xFFFF) ^ (len(payload) * c)) & 0xFFFFFFFF
    if struct.pack(">I", folded) != crc:
        raise LinkError("the link's checksum doesn't match (incomplete or changed link)")
    item = {"defindex": 0, "paintindex": 0, "paintwear": 0.0, "paintseed": 0, "stickers": [], "keychains": []}
    for field, v in _fields(payload):
        if field in _ITEM:
            item[_ITEM[field]] = v
        elif field in (12, 20):
            st = {"slot": 0, "sticker_id": 0, "wear": 0.0, "rotation": 0.0, "offset_x": 0.0, "offset_y": 0.0,
                  "scale": 0.0}
            for sf, sv in _fields(v):
                if sf in _STICKER:
                    st[_STICKER[sf]] = sv
            item["stickers" if field == 12 else "keychains"].append(st)
    # paintwear is stored as the float's raw bits in a varint
    if isinstance(item["paintwear"], int):
        item["paintwear"] = struct.unpack("<f", struct.pack("<I", item["paintwear"] & 0xFFFFFFFF))[0]
    return item
