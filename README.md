# CS2 Weapon Sticker Shader for Blender

A node-based recreation of the CS2 `csgo_weapon_sticker` ("CSGO Weapon Sticker Preview") pixel shader, rebuilt from the decompiled Vulkan source.

## Install

1. Download `cs2_weapon_sticker.zip` from the [latest release](https://github.com/lollpdesu/cs2-weapon-sticker-blender/releases/latest).
2. Blender 4.2 or newer: **Edit > Preferences > Add-ons > ⌄ (top right) > Install from Disk…** and pick the zip.

When updating, restart Blender before installing the new zip: Windows keeps the add-on's helper library (`pylib.pyd`) locked while it is loaded.

To remove it, open the add-on's preferences (**Edit > Preferences > Add-ons > CS2 Weapon Sticker Shader**) and click **Uninstall Add-on**. Your .blend files and their sticker materials are kept. On Windows, restart Blender afterwards to finish removing the helper library.

Tested on Windows with Blender 5.1. The helper library is included for Linux and macOS as well, but those platforms have not been tested yet.

To build the zip yourself: `blender --command extension build --source-dir cs2_weapon_sticker`.

## Sticker Browser

**3D Viewport > Sidebar (N) > CS2 Sticker**

Imports any sticker straight from your Counter-Strike 2 install, with its textures and the exact settings from its in-game material.

1. The CS2 folder is found automatically through Steam. If not, set it under **Edit > Preferences > Add-ons > CS2 Weapon Sticker Shader** (the folder that contains `game/csgo/pak01_dir.vpk`).
2. The sticker list loads by itself a moment after Blender starts. The first time it reads `items_game.txt` and the English names (about 1 second). After that it comes from a cache.
   * **Automatic updates:** once a minute the add-on checks whether a CS2 update changed the game files. If so, it rebuilds the list in the background. Stickers added by the update are shown in a banner and under **Collection > New in Latest CS2 Update**. You can turn this off in the add-on preferences (**Update Sticker List Automatically**); the refresh button at the bottom of the panel rebuilds the list by hand.
3. Search by name, team, player or tournament, and filter by **Collection** and **Type** (Paper, Glitter, Holo, Foil, Gold, Lenticular, Embroidered). Collections use the game's own capsule names; community capsules are listed one by one (for example *Fruits And Veggies Sticker Collection*, *Jackass Sticker Capsule*). Thumbnails are the game's own preview images, with transparent backgrounds.
4. Click a sticker, choose **Import To**, set an initial **Wear**, and click **Import Sticker**:
   * *New Plane*: a plane with the sticker's aspect ratio at the 3D cursor.
   * *Selected Objects*: the sticker material on the selected objects' active slot, using their own UVs (legacy CS:GO sticker meshes).
   * *Weapon (CS2 Model)*: the sticker layered into a sticker slot of the active weapon's own material, see below.
   * *Console Link*: every sticker of a CS2 console link, placed exactly like on that item, see below.

What the import does:

* Reads the sticker's compiled material (`stickers/<name>.vmat_c`) and sets every parameter it defines: Metallic, Holographic, Glitter, Color Boost, tint, Wear Bias, Wear Scratches and so on. Anything the material doesn't set keeps the shader default.
* Decodes the compiled textures (`.vtex_c`) and fills every texture slot. Channels that the shader reads separately (sticker roughness, self-illum mask, glitter mask, glitter normal mask) become their own grayscale images. **Invert Glitter Mask** is switched on because the game stores that mask inverted.
* Images are packed into the .blend, so the file works without CS2. Shared textures (scratches, default masks) are reused across stickers.
* Wear is not part of the material (in-game it belongs to the item), so it uses the value you set before importing.

Sprays (graffiti) and stickers without a sticker material are not listed.

### Stickers on CS2 weapon models

Legacy CS:GO models carry a separate sticker mesh with its own UVs. CS2 models don't: the sticker is placed on the weapon's second UV map (`TEXCOORD_1`), at a fixed position per weapon and slot, and a sticker mask on the weapon limits where stickers may appear.

The slots are read from the weapon model in your game files (`weapons/models/<weapon>/weapon_*.vmdl_c`, its `StickerMarkup` key values), so they are exact and follow CS2 updates.

1. Select the weapon model and set **Import To** to *Weapon (CS2 Model)*.
2. **Weapon**: pick the weapon. 34 weapons are included.
3. **Model**: *CS2* for the current weapon model, *Legacy* for the legacy model that legacy finishes use. The two models have their own slot positions and sizes, and their own sticker mask (`_sticker_mask_hd` or `_sticker_mask_legacy`; 21 weapons have a legacy mask).
4. **Slot**: numbered 1 to 4 (5 on the G3SG1, M4A1-S, USP-S and R8, 6 on the MP5-SD) from the best to the least valued position, following pinkxzy's community guides ([CS2](https://steamcommunity.com/sharedfiles/filedetails/?id=3168600963), [legacy](https://steamcommunity.com/sharedfiles/filedetails/?id=2590458348)); for example *Slot 1* on the AWP is the scope. Hover a slot to see its position, size and rotation. Switching **Model** keeps the slot number and uses that model's values. **UV Map**: empty uses the second UV map (or the first if there is only one).
5. **X / Y / R** (under Wear): an optional custom placement, like moving and rotating a sticker in the game before applying it. X and Y move the sticker from the slot's position along the weapon's UV map (Y negative = towards the top), R adds degrees to the slot's rotation. These are the same numbers a console link stores. Leave them at 0 for the slot's own position.
6. **Import Sticker**. Repeat for the other slots; importing into a slot that is already used replaces that sticker.

### Console Link import

Places all stickers of a real (or crafted) CS2 item on the weapon in one go, including custom positions, rotations and scrape levels.

1. Set **Import To** to *Console Link* and paste the item's console link into **Link**. It looks like `csgo_econ_action_preview 00180920F405…` (a steam:// link with the same hex code works too). The link is read offline; nothing is downloaded.
2. Choose the weapon model:
   * **Selected Model**: the weapon model you already have in the scene (select it first).
   * **Import from Game**: the add-on imports the weapon model straight from your CS2 files, named after the skin (for example *AWP | Gungnir*). Each CS2 weapon file holds the CS2 and the legacy model; only the one the skin uses is imported, with both UV maps and the game's normals. The skin's textures are not part of the import; the model gets a plain grey material.
3. **Import from Link**.

The link decides everything else: the weapon, whether the skin uses the legacy model (read from `items_game.txt`, `use_legacy_model`), each sticker, its slot, its offset and rotation, and its wear. A custom placement is applied the way the game does it: the offset is added to the slot's position and the rotation (in degrees) to the slot's rotation; the size stays the slot's. Moved stickers can share a slot number in the link (for example two stickers both in slot 2); each still gets its own layer. Importing the same link again replaces those stickers instead of adding them twice; each *Import from Game* creates a new model with its own material.

Old-style inspect links (`…S76561…A…D…`) only point at an item on Valve's servers and contain no sticker data, so they can't be used. Importing the model from the game is only offered for console links; for the other targets, import or prepare the weapon model yourself.

No new material or object is created. The sticker goes into the weapon's own (active) material, in one **CS2 Weapon Stickers** node between the weapon's shader and the Material Output:

* **BSDF**: the weapon's shader (wired automatically); the stickers are drawn over it.
* **Mask**: the weapon's sticker mask. The add-on loads `weapons/models/<weapon>/materials/stickers/*_sticker_mask_hd` (or `*_sticker_mask_legacy` for the legacy model) from the game and connects its red channel, sampled with the weapon's first UV map.
* **NormalMap**: connect the weapon's normal map here (Image Texture, Non-Color) so the stickers follow its bumps.

Inside the group, every slot is its own sticker group (*Slot 1 | …*, *Slot 2 | …*), chained in slot order. Press **Tab** on the node to open it and change a sticker's settings, for example its **Wear**, on its slot node.

The placement is set on each slot node in its **Sticker Placement** panel and can be changed afterwards:

* **UV Placement**: on for CS2 models; off (the default) uses the Sticker UV as is, for planes and legacy sticker meshes.
* **Slot X / Slot Y**: offset of the sticker's center from the middle of the UV map (Y negative = towards the top, as in CS2).
* **Slot Scale**: how many times smaller than the whole UV map the sticker is.
* **Rotation**: rotation in degrees, counter-clockwise. Some slots are rotated in the game, for example the grip slot on most pistols (Glock-18: 22.7°). The game stores it in turns (`g_flStickerNRotation` × 2π); the add-on converts it.

The sticker UV is `(uv - (0.5 + X, 0.5 - Y)) * Scale + 0.5`, rotated around the sticker's center.

## Use

**Properties > Material > CS2 Weapon Sticker**

* **New CS2 Sticker Material** creates a material and puts it in the active slot.
* **Add Sticker Shader to Material** adds the shader to an existing material and connects it to the output. Your other nodes stay.
* If the scene has a Sun lamp it is picked automatically. Its direction drives the holographic spectrum lookup through a driver.
* **Rebuild Node Tree** regenerates the graph and keeps your parameters, links and textures.

Every material gets its own node group, `CS2 Weapon Sticker [<material>]`. The group outputs a ready-to-use **BSDF**. It also outputs the raw channels (Base Color, Metallic, Roughness, Normal, Emission, Glitter, Sticker Mask) if you want to build your own lighting or bake textures. These channels are the sticker's own values, not blended with a surface underneath; use **Sticker Mask** as their alpha (it is the sticker's coverage, including the soft edge and wear).

Files made with an older version of the add-on are updated automatically when you open them: sticker materials get the new node graph and keep their settings and textures.

## Textures

Each mask and roughness map has its own slot. Grayscale masks are read from the red channel. The addon sets the color space and alpha mode for each slot.

| Slot | Shader source | If left empty |
|---|---|---|
| Base Roughness | `g_tNormal` B | 0.5 |
| Sticker Color | `g_tSticker0` RGB (sRGB) | no sticker |
| Wear Mask | `g_tSticker0` A (also the sticker's coverage) | the Sticker Color image's alpha |
| Sticker Normal | `g_tNormalRoughnessSticker0` RG | flat |
| Sticker Roughness | `g_tNormalRoughnessSticker0` B | **0.1** |
| Self Illum Mask | `g_tNormalRoughnessSticker0` A | 0 |
| Holo Mask | `g_tSfxMaskSticker0` RGB (R mask, G/B spectrum offset) | 0 |
| Holo Spectrum | `g_tHoloSpectrumSticker0` (sRGB) | rainbow placeholder |
| Glitter Mask | `g_tSfxMaskSticker0` A, white = glitter | no glitter (with **Invert Glitter Mask** on: glitter everywhere) |
| Glitter Normal | `g_tGlitterNormalSticker0` RG | flat |
| Glitter Normal Mask | `g_tGlitterNormalSticker0` A | 1 |
| Sticker Scratches | `g_tStickerScratches` R | none |

The compiled `g_tSfxMaskSticker0` alpha stores the glitter mask inverted (black = glitter). If your glitter mask comes from that channel, turn on **Invert Glitter Mask** under its slot.

**Foil Using Glitter Mask** (`g_bFoilUsingGlitterMaskSticker0`, SFX panel): added with the Singapore 2026 stickers. When on, the glitter mask's black areas also count as metal, so a foil sticker can be metallic only in parts instead of everywhere. The import sets it from the sticker's material.

If your sticker normal maps are regular RGB normal maps rather than raw hemi-octahedral RG, set **Normal Format** to *Standard RGB*.

### Weapon normal map (Base Weapon)

The normal map of the weapon under the sticker (`g_tNormal`) is a node input: open the group node's **Base Weapon** panel and connect an Image Texture (set to *Non-Color*) to **Base Normal**, sampled with the weapon's UV. The sticker's own normal is built on top of it, so bumps and scratches in the weapon surface show through the sticker. Left unconnected, the surface is flat.

* **Base Normal Hemi-Oct**: turn on for a raw compiled CS2 normal map (hemi-octahedral RG), leave off for a regular RGB normal map.
* **Base Normal DirectX**: turn on for DirectX-style RGB normal maps (flipped green).

* **Base Normal Strength**: how strongly the weapon's bumps show through the sticker. Default 0.1 (a sticker smooths over the surface); 1 = full strength.

This is independent of the sticker's **Normal Format**. Files from 1.0.0 that had a texture in the old *Base Normal* slot are converted automatically when opened.

The material is transparent everywhere the sticker isn't drawn, including worn-away areas, so the plane works as a decal. The holo spectrum's sun direction comes from the **Sun** picker in the panel, through a driver on a node inside the group. Without a sun set, it points mostly straight up.

Parameters use the same names as the material's `g_*` values. For example, *Wear* is `g_flSticker0Wear`. On/off switches are 0/1 values.

## Exactness

The full surface computation is a direct translation. That covers PBR color fitting (normal, legacy tint, metallic and dark-metallic levels), tint and saturation, color boost, glitter, holographic spectrum, self-illum, wear, scratches, paper backing, roughness preservation, the sticker edge sample and normal blending. It was checked numerically against a Python port of the GLSL and matches to within 2×10⁻⁶.

These parts can't be reproduced one-to-one in Blender:

* **Lighting:** CS2's lighting engine (light binner, cubemaps, lightmaps, BRDF lookup table, fog) is replaced by Blender's renderer. The shader's outputs feed a Principled BSDF with GGX and F0 = 0.04, which is the same material model. Glitter is added as a Diffuse BSDF on top, matching how the engine adds it on top of the lit result using the diffuse lighting.
* **Mip-level effects:** Blender shader nodes can't read mip levels or screen-space derivatives. The base normal and AO under the sticker are read at full resolution instead of mip ≥ 3, and the glitter distance fade is treated as close up (factor 1).
* **Editor-only features are left out:** the peel animation, mouse-over and drag highlight, store mode, wear preview, and the level-load flash.
* **Holo vs. glitter mask:** in the game, glitter areas sample the holo mask and spectrum pixel-sharp. That looks broken in Blender close-ups, so the holo always uses smooth sampling and the glitter mask only affects glitter. The Holo Spectrum uses Smart interpolation.
* **Not included:** the weapon surface under the sticker and ambient occlusion. Areas outside the sticker are transparent instead. The game blends color, roughness, metalness, emission and glitter with that surface by the sticker's coverage; here the same coverage drives the transparency (once), so whatever is behind the decal takes the surface's place.

## Credits

* Reading the game files uses a small part of **[SourceIO](https://github.com/REDxEYE/SourceIO)** by REDxEYE (MIT License, see `cs2_weapon_sticker/sourceio/LICENSE`): its native helper library (VPK reader, BCn texture decoding, LZ4/Zstd) and its binary KeyValues3 reader.
* File layouts follow **[ValveResourceFormat / Source 2 Viewer](https://github.com/ValveResourceFormat/ValveResourceFormat)** (MIT License).
* The slot order (best position first) follows the community guides by **pinkxzy** on Steam ([CS2](https://steamcommunity.com/sharedfiles/filedetails/?id=3168600963), [legacy](https://steamcommunity.com/sharedfiles/filedetails/?id=2590458348)).
* Earlier versions used the sticker slot table from **[KeisariKine/cs2-stickers-blender](https://github.com/KeisariKine/cs2-stickers-blender)**; since 1.3.0 the slots are read from the game files.
* Not affiliated with or endorsed by Valve. Counter-Strike and CS2 are trademarks of Valve Corporation. The add-on reads your own game install; no game files are included.

## License

MIT, see [LICENSE](LICENSE). The bundled SourceIO subset in `cs2_weapon_sticker/sourceio/` is also MIT licensed, by REDxEYE (see its own LICENSE file).
