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
3. Search by name, team, player or tournament, and filter by **Collection** and **Type** (Paper, Glitter, Holo, Foil, Gold, Lenticular, Embroidered). Thumbnails are the game's own preview images.
4. Click a sticker, choose **Import To** (*New Plane* with the sticker's aspect ratio at the 3D cursor, or *Selected Objects*), set an initial **Wear**, and click **Import Sticker**.

What the import does:

* Reads the sticker's compiled material (`stickers/<name>.vmat_c`) and sets every parameter it defines: Metallic, Holographic, Glitter, Color Boost, tint, Wear Bias, Wear Scratches and so on. Anything the material doesn't set keeps the shader default.
* Decodes the compiled textures (`.vtex_c`) and fills every texture slot. Channels that the shader reads separately (sticker roughness, self-illum mask, glitter mask, glitter normal mask) become their own grayscale images. **Invert Glitter Mask** is switched on because the game stores that mask inverted.
* Images are packed into the .blend, so the file works without CS2. Shared textures (scratches, default masks) are reused across stickers.
* Wear is not part of the material (in-game it belongs to the item), so it uses the value you set before importing.

Sprays (graffiti) and stickers without a sticker material are not listed.

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

If your sticker normal maps are regular RGB normal maps rather than raw hemi-octahedral RG, set **Normal Format** to *Standard RGB*.

### Weapon normal map (Base Weapon)

The normal map of the weapon under the sticker (`g_tNormal`) is a node input: open the group node's **Base Weapon** panel and connect an Image Texture (set to *Non-Color*) to **Base Normal**, sampled with the weapon's UV. The sticker's own normal is built on top of it, so bumps and scratches in the weapon surface show through the sticker. Left unconnected, the surface is flat.

* **Base Normal Hemi-Oct**: turn on for a raw compiled CS2 normal map (hemi-octahedral RG), leave off for a regular RGB normal map.
* **Base Normal DirectX**: turn on for DirectX-style RGB normal maps (flipped green).

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
* Not affiliated with or endorsed by Valve. Counter-Strike and CS2 are trademarks of Valve Corporation. The add-on reads your own game install; no game files are included.

## License

MIT, see [LICENSE](LICENSE). The bundled SourceIO subset in `cs2_weapon_sticker/sourceio/` is also MIT licensed, by REDxEYE (see its own LICENSE file).
