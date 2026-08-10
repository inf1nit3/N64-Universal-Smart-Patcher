# SM64 Hi-Res Patch Analysis (Phase 2 findings)

Analysis date: 2026-08-09
Patch analyzed: `N64noAAPatcher/hires_patches/Super Mario 64 (U) [!] 640 x 480i No AA[SubDrag].xdelta`
ROM tested: clean `Super Mario 64 (USA)` dump (SHA1 `9bef1128...`, 8 MB).

## 1. What the patch changes (verified byte-for-byte)

Applying the 1032-byte delta to the clean ROM produces **232 changed regions /
441 changed bytes**. Classified by function:

| Change | Count | Interpretation |
|---|---|---|
| `0x140→0x280`, `0x1E0→0x3C0`, `0x500→0xA00`, `0x3C0→0x780`, `0x3A0→0x740` | ~24 | Doubled fixed-point screen coords (RDP scissor / fill-rect / viewport in S10.2 units) |
| `0x50→0xA0`, `0x78→0xF0`, `0xA0→0x140` (u16) | ~120 | Doubled 320×240-space pixel constants (HUD/menu positions, widths) |
| `0x1F→0x3F`-style +1 values (`0x13F→0x27F`, `0x4F→0x9F`) | ~15 | Doubled "width−1" / half-width style bounds |
| `0x311E→0x335E` @0xF00B6, `0x140→0x500` @0xF00BA | VI mode struct: control bits + width | SM64's custom `OSViMode` (width 320→1280 in internal units, ctrl bit changes) |
| `0xE8B30` viewport structs `{640,480,511,0}×2 → {1280,960,511,0}×2` | 2 structs | `D_8032CF00` and a second viewport doubled |
| Framebuffer addresses `0x8000…→0x8060…`, sizes `0x25800→0x96800` | @0xFBAF3–0xFBB27 | Move/double framebuffer & z-buffer to Expansion Pak area |
| CRC1/CRC2 in header | 8 bytes | Recalculated boot checksums |
| `0xDE4DE` 1 byte `0xFC→0xFF` | 1 | Minor timing/flag tweak |

166 of the 232 regions lie **inside MIO0-compressed segments** (SubDrag
decompressed, modified and recompressed them — that's why the delta is so
small: only the changed literals survive as xdelta COPY+ADD records).

## 2. Why the menus/HUD are still misaligned ("verrutscht")

SM64's 2D HUD and menus are **not** drawn through the projection/viewport
pipeline. Text glyphs, icons, dialogs, pause-menu boxes etc. are emitted as
**`gSPTextureRectangle` / `gDPFillRectangle` commands with absolute 320×240
pixel coordinates** (see `print.c`, `hud.c`, `ingame_menu.c` in the sm64
decomp). In a 640×480 framebuffer these stay at their old 320×240 positions:

- HUD text at `x=22` stays at pixel `22` of a 640-wide screen → appears
  half-size, stuck in the top-left corner → "menus shifted".
- Geometry rendered through the (doubled) viewport *does* scale, so the
  mismatch is inconsistent per element — some scale, some don't.

The patch doubles the *viewport/scissor/framebuffer* plumbing but only
**some** of the literal HUD constants (the ones whose byte patterns look like
clean 2× values). Constants that don't encode as clean doubles (e.g. values
with offsets like `+16`, `+1`, computed positions, positions stored inside
compressed level/actor segments) were missed.

## 3. What a complete menu fix requires

A full fix = locate **every** 2D screen-coordinate constant in the game code
and HUD display lists and double it (and fix `width−1` style values), both in
the uncompressed main segment **and** inside the MIO0-compressed segments.
This is per-game reverse-engineering of hundreds of constants and needs
visual verification on hardware/emulator after each iteration. This is the
reason SubDrag's own README states: *"they may not fully work throughout the
entire game, menus will be messed up"*.

**Suggested tool-level solution** (matches this project's architecture):
a `game_fixes` stage in `n64_core` that applies an IPS/BPS "menu fix" patch
on top of the SubDrag xdelta for titles where the community fix exists, keyed
by CRC1. The infrastructure is reusable; the SM64 menu-fix IPS itself is a
separate RE deliverable that should be built incrementally with hardware
testing.

## 4. Tooling available now

- `xdelta_patch.py` — built-in pure-Python VCDIFF engine (v3.2), applies the
  SubDrag patch on any platform; Adler-32 verified against the clean dump.
- `describe_xdelta_patch` is available in `xdelta_patch.iter_windows()` for
  building diff/analysis tooling.
- `/tmp/sm64-decomp` (sm64 decompilation clone) and `/tmp/mipsdis.py`
  (minimal MIPS disassembler) were used for this analysis.
