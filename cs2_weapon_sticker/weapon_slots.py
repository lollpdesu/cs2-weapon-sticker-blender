"""Weapons that take stickers, and where their slot data lives in the game files.

The sticker slots themselves are read from the weapon model in the user's CS2 install
(weapons/models/<folder>/weapon_*.vmdl_c, key values -> StickerMarkup), separately for the
CS2 (body_hd) and the legacy (body_legacy) model. See cs2data.read_sticker_slots.
"""

# key: (display name, folder under weapons/models/)
WEAPONS = {
    'ak47': ('AK-47', 'ak47'),
    'aug': ('AUG', 'aug'),
    'awp': ('AWP', 'awp'),
    'bizon': ('PP-Bizon', 'bizon'),
    'cz75a': ('CZ75-Auto', 'cz75a'),
    'deagle': ('Desert Eagle', 'deagle'),
    'elite': ('Dual Berettas', 'elite'),
    'famas': ('FAMAS', 'famas'),
    'fiveseven': ('Five-SeveN', 'fiveseven'),
    'g3sg1': ('G3SG1', 'g3sg1'),
    'galilar': ('Galil AR', 'galilar'),
    'glock': ('Glock-18', 'glock18'),
    'hkp2000': ('P2000', 'hkp2000'),
    'm249': ('M249', 'm249'),
    'm4a1_silencer': ('M4A1-S', 'm4a1_silencer'),
    'm4a4': ('M4A4', 'm4a4'),
    'mac10': ('MAC-10', 'mac10'),
    'mag7': ('MAG-7', 'mag7'),
    'mp5sd': ('MP5-SD', 'mp5sd'),
    'mp7': ('MP7', 'mp7'),
    'mp9': ('MP9', 'mp9'),
    'negev': ('Negev', 'negev'),
    'nova': ('Nova', 'nova'),
    'p250': ('P250', 'p250'),
    'p90': ('P90', 'p90'),
    'revolver': ('R8 Revolver', 'revolver'),
    'sawedoff': ('Sawed-Off', 'sawedoff'),
    'scar20': ('SCAR-20', 'scar20'),
    'sg556': ('SG 553', 'sg556'),
    'ssg08': ('SSG 08', 'ssg08'),
    'tec9': ('Tec-9', 'tec9'),
    'ump45': ('UMP-45', 'ump45'),
    'usp_silencer': ('USP-S', 'usp_silencer'),
    'xm1014': ('XM1014', 'xm1014'),
}


# Slot order shown in the add-on: Slot 1 = best position ... per the community guide by pinkxzy
# (steamcommunity.com/sharedfiles/filedetails/?id=3168600963; legacy FAMAS/MP7/P90/Nova: ?id=2590458348),
# matched to the game's StickerMarkup slots by their place on each model. Values = game slot index.
# Slots the game adds later and that are not listed here are appended in game order.
SLOT_ORDER = {
    'ak47': {'hd': [3, 0, 1, 2], 'legacy': [3, 0, 1, 2]},  # AK-47
    'aug': {'hd': [2, 4, 1, 0], 'legacy': [2, 4, 1, 0]},  # AUG
    'awp': {'hd': [3, 2, 1, 0], 'legacy': [3, 2, 4, 0]},  # AWP
    'bizon': {'hd': [0, 2, 1, 3], 'legacy': [0, 2, 1, 3]},  # PP-Bizon
    'cz75a': {'hd': [4, 0, 1, 3], 'legacy': [2, 5, 4, 3]},  # CZ75-Auto
    'deagle': {'hd': [1, 3, 2, 4], 'legacy': [1, 3, 2, 0]},  # Desert Eagle
    'elite': {'hd': [1, 2, 3, 0], 'legacy': [1, 2, 3, 0]},  # Dual Berettas
    'famas': {'hd': [2, 3, 0, 4], 'legacy': [2, 3, 5, 4]},  # FAMAS
    'fiveseven': {'hd': [0, 4, 5, 3], 'legacy': [4, 5, 6, 3]},  # Five-SeveN
    'g3sg1': {'hd': [4, 1, 3, 2, 0], 'legacy': [4, 1, 3, 2, 0]},  # G3SG1
    'galilar': {'hd': [0, 1, 2, 3], 'legacy': [0, 1, 2, 3]},  # Galil AR
    'glock': {'hd': [0, 2, 1, 3], 'legacy': [4, 2, 1, 3]},  # Glock-18
    'hkp2000': {'hd': [0, 5, 4, 3], 'legacy': [4, 2, 1, 3]},  # P2000
    'm249': {'hd': [1, 2, 0, 3], 'legacy': [1, 2, 0, 3]},  # M249
    'm4a1_silencer': {'hd': [1, 2, 0, 3, 4], 'legacy': [1, 2, 0, 3, 4]},  # M4A1-S
    'm4a4': {'hd': [2, 0, 1, 3], 'legacy': [6, 4, 5, 3]},  # M4A4
    'mac10': {'hd': [0, 2, 3, 4], 'legacy': [0, 2, 3, 1]},  # MAC-10
    'mag7': {'hd': [1, 0, 2, 3], 'legacy': [1, 0, 2, 3]},  # MAG-7
    'mp5sd': {'hd': [4, 3, 2, 1, 0, 5], 'legacy': [4, 5, 6, 1, 0, 7]},  # MP5-SD
    'mp7': {'hd': [0, 4, 1, 5], 'legacy': [1, 4, 0, 5]},  # MP7
    'mp9': {'hd': [0, 2, 1, 3], 'legacy': [0, 2, 1, 4]},  # MP9
    'negev': {'hd': [0, 1, 2, 3], 'legacy': [0, 1, 2, 3]},  # Negev
    'nova': {'hd': [1, 2, 3, 4], 'legacy': [1, 2, 3, 4]},  # Nova
    'p250': {'hd': [0, 5, 4, 3], 'legacy': [4, 6, 5, 3]},  # P250
    'p90': {'hd': [1, 4, 0, 2], 'legacy': [1, 4, 0, 2]},  # P90
    'revolver': {'hd': [1, 2, 0, 3, 4], 'legacy': [1, 2, 0, 3, 4]},  # R8 Revolver
    'sawedoff': {'hd': [0, 1, 2, 4], 'legacy': [0, 1, 2, 4]},  # Sawed-Off
    'scar20': {'hd': [2, 3, 1, 0], 'legacy': [2, 3, 1, 0]},  # SCAR-20
    'sg556': {'hd': [2, 1, 0, 3], 'legacy': [2, 1, 0, 3]},  # SG 553
    'ssg08': {'hd': [2, 3, 1, 0], 'legacy': [2, 3, 1, 0]},  # SSG 08
    'tec9': {'hd': [0, 1, 2, 3], 'legacy': [0, 1, 4, 3]},  # Tec-9
    'ump45': {'hd': [3, 2, 1, 0], 'legacy': [3, 2, 1, 0]},  # UMP-45
    'usp_silencer': {'hd': [1, 3, 2, 4, 0], 'legacy': [1, 3, 2, 4, 0]},  # USP-S
    'xm1014': {'hd': [0, 1, 2, 4], 'legacy': [0, 1, 2, 4]},  # XM1014
}


def display(key):
    return WEAPONS[key][0]


def folder(key):
    return WEAPONS[key][1]


def key_for_classname(classname):
    """Weapon key for an item class name such as "weapon_awp" (the M4A4 is "weapon_m4a1"), or None."""
    name = (classname or "").lower()
    if name == "weapon_m4a1":
        return "m4a4"
    key = name[len("weapon_"):] if name.startswith("weapon_") else name
    return key if key in WEAPONS else None
