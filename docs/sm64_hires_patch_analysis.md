# SM64 Hi-Res Patch Analysis

Patch analyzed: `N64noAAPatcher/hires_patches/Super Mario 64 (U) [!] 640 x 480i No AA[SubDrag].xdelta`
ROM: clean `Super Mario 64 (USA)`, SHA-1 `9bef1128717f958171a4afac3ed78ee2bb4e86ce`,
CRC1 `635A2BFF`, CRC2 `8B022326`, 8 MB.

First pass 2026-08-09. **Revised 2026-08-11**: the first pass got two things
wrong, and both changed what the work is. They are corrected in place below,
with the correction noted, because the wrong figures were the reason this was
filed as a hundreds-of-constants reverse-engineering job.

## 1. What the patch changes

Applying the 1032-byte delta to the clean ROM produces **232 changed regions /
441 changed bytes**. That figure is reproducible with
`scripts/sm64_hires/report.py`.

Classified by where they land:

| Where | Regions | What |
|---|---|---|
| Main code segment (ROM `0x1000`–`0x101000`) | 65 | Engine plumbing — see below |
| Geo layouts, uncompressed level/actor data | ~160 | `GEO_NODE_SCREEN_AREA` nodes |
| ROM header | 1 (8 bytes) | Recalculated CRC1/CRC2 |

The geo-layout changes are all one edit repeated: `08 00 000A 00A0 0078 00A0
0078` → `… 0140 00F0 0140 00F0`, i.e. `GEO_NODE_SCREEN_AREA(10, 160, 120,
160, 120)` → `(10, 320, 240, 320, 240)`. One per level and actor layout.

> **Correction.** The first pass reported "166 of the 232 regions lie inside
> MIO0-compressed segments" and inferred that SubDrag had decompressed,
> edited and recompressed them. That is wrong. These regions are plain
> uncompressed geo-layout commands — they diff as perfectly aligned 8-byte
> structures, which is not something compressed data does. The delta is small
> because the edit is small and repetitive, not because it survived
> recompression.

The 65 main-segment changes are, in full:

| Change | Count | What |
|---|---|---|
| `lui`/`ori` pairs building doubled S10.2 coordinate words | ~20 | RDP scissor / fill-rect / viewport constants |
| `addiu` immediates 160→320, 120→240, 320→640, 240→480 | ~12 | `SCREEN_WIDTH`/`SCREEN_HEIGHT` and their halves, passed as arguments |
| Viewport structs `{640,480,511,0}` → `{1280,960,511,0}` at `0xE8B30`, `0xE8E60` | 2 | `Vp` vscale, doubled |
| `OSViMode` at `0xF00B6` | 8 | Control bits `0x311E`→`0x335E`, width `0x140`→`0x500`, x-scale, related fields |
| Framebuffer/z-buffer addresses and sizes at `0xFBAF3`–`0xFBB27` | 10 | Moved and enlarged into the Expansion Pak area |
| `0xDE4DE`, one byte `0xFC`→`0xFF` | 1 | Flag/timing tweak |

> **Correction.** The first pass reported "~120 doubled 320×240-space pixel
> constants (HUD/menu positions, widths)". There are no such changes. **The
> delta does not touch the 2D layer at all** — not one HUD or menu drawing
> constant is modified. Every main-segment change is engine-level.

## 2. Why the menus and HUD are misaligned

SM64's 2D layer is not drawn through the projection/viewport pipeline. Text
glyphs, icons, dialog boxes and menu panels are emitted as
`gSPTextureRectangle` / `gDPFillRectangle` commands carrying **raw screen
coordinates** (`print.c`, `hud.c`, `ingame_menu.c` in the decompilation).
RDP rectangle commands bypass the RSP transform entirely, so nothing the
delta changes reaches them.

In a 640×480 framebuffer those elements therefore stay where 320×240 put
them, at their original size: half-scale, crowded into the upper-left
quadrant, while everything drawn through the viewport scales correctly. The
mismatch is per-element, which is what makes it read as "menus shifted".

This is the same limitation SubDrag documents: *"they may not fully work
throughout the entire game, menus will be messed up"*.

## 3. What a fix requires

The first pass concluded this needed every 2D constant in the game located
and doubled, "hundreds of constants ... per-game reverse engineering". That
followed from the two errors above and is not the case.

Coordinates cannot be scaled by a matrix, but they are all converted to the
RDP's 10.2 fixed-point form by a shift — `x << 2` — immediately before being
packed into the command word. Changing that shift to `x << 3` doubles the
coordinate and leaves it in 10.2, costing no instructions and no space.
Halving the texture step (`dsdx`, `dtdy`) in the same command makes the
texture stretch over twice the pixels rather than repeat.

Six word edits per emitter. The whole ROM contains **seven** texture-
rectangle emitters, found by signature rather than by search: a `sll rd, rt,
2` whose result feeds an `andi rX, rd, 0xFFF`. 42 words in total.

Sites 1–4 (the menu glyph renderers) are confirmed correct on hardware and
**ship** as `game_fixes/635A2BFF_sm64_menu_2x.ips`, applied automatically
by Stage 1b after the SubDrag delta. The three HUD sites are not yet: with
all seven enabled the in-game HUD renders its icons but loses its numbers.
Working notes, the bisect variants and the leading hypothesis — 12-bit
field overflow on coordinates derived from an already-doubled screen
width — are in
[`scripts/sm64_hires/README.md`](../scripts/sm64_hires/README.md).

The delivery mechanism is Stage 1b in `n64_core.py`
(`get_game_fix_for_rom`), which applies an IPS/BPS keyed on the clean ROM's
CRC1 on top of a delta that applied. That stage is finished and tested.

## 4. Tooling

`scripts/sm64_hires/` — the disassembler, the delta report, the emitter
finder and the patch builder, with instructions for recreating the working
ROMs from a clean dump. No ROMs are in the repository.
