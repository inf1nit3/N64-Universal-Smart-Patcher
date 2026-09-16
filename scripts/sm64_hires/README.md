# SM64 640x480 2D fix — menus shipped, HUD open

Research tooling for the task described in
[`docs/sm64_hires_patch_analysis.md`](../../docs/sm64_hires_patch_analysis.md):
making Super Mario 64's menus and HUD render correctly on top of the
verified SubDrag 640x480 delta.

**Shipped:** `src/n64patcher/game_fixes/635A2BFF_sm64_menu_2x.ips` carries
the four hardware-confirmed menu emitters (sites 1-4 below). Stage 1b
applies it automatically on top of the SubDrag delta; a normal `--hires`
run logs `Game fix: applied 635A2BFF_sm64_menu_2x.ips`. It is rebuilt
from `635A2BFF_sm64_hud_textrect_2x.bps.wip` by `make_ips.py`, which
refuses to write anything if the .wip and the site table disagree.

**Still open:** the three HUD sites. With all seven enabled the in-game
HUD loses its numbers, so the shipped fix deliberately excludes them.

**Note for this machine:** a user-level fix in
`~/.n64patcher/game_fixes/` overrides the shipped one. A test build left
there during the hardware runs (all seven sites) shadows the menu fix -
remove or rename it for normal use.

## The approach

RDP rectangle commands carry raw screen coordinates and bypass the
transform pipeline, so no matrix can scale the 2D layer — which is why the
original analysis concluded this needed hundreds of hand-found constants.
What *can* be scaled is the shift that converts a pixel coordinate into the
RDP's 10.2 fixed-point form. The code already does `x << 2`; making it
`x << 3` doubles the coordinate and leaves it in 10.2, at zero instruction
cost. Halving the texture step (dsdx, dtdy) alongside makes the glyph
stretch over twice the pixels instead of repeating.

That is six word edits per texture-rectangle emitter, and the whole ROM
contains seven of them. 42 words, not hundreds of constants.

## State

Verified on real hardware (SummerCart64, 2026-08-11):

| Site | ROM offset | Draws | Status |
|---|---|---|---|
| 0 | `0x00091BB8` | HUD font: coins, stars, lives, timer | suspect |
| 1 | `0x00092D80` | menu glyph | **correct** |
| 2 | `0x00093020` | menu glyph, 2nd form | **correct** |
| 3 | `0x000931A4` | menu glyph, 3rd form | **correct** |
| 4 | `0x00093520` | menu glyph, 4th form | **correct** |
| 5 | `0x0009DDB4` | HUD LUT char: large digits, icons | suspect |
| 6 | `0x0009E010` | HUD LUT char, 2nd form | suspect |

Sites 1–4 were confirmed by photographing the file-select screen and the
Peach letter: labels, titles and dialog text all land at the right size and
position, where before the fix they sat half-size in the upper left. Those
four are what ships.

With all seven enabled the in-game HUD loses its **numbers** — the icons
render, the digits do not. One of sites 0, 5, 6 is responsible. The bisect
runs through the normal pipeline - each variant is an installable game fix:

```bash
python scripts/sm64_hires/make_ips.py --bisect
# writes scripts/sm64_hires/bisect/ :
#   635A2BFF_bisect_A_menus.ips          sites 1-4   (the shipped set)
#   635A2BFF_bisect_B_plus_hud_font.ips  sites 0-4   (+ HUD font)
#   635A2BFF_bisect_C_plus_hud_lut.ips   sites 1-6   (+ HUD LUT)

# install one variant at a time, replacing the shipped fix:
cp scripts/sm64_hires/bisect/635A2BFF_bisect_B_plus_hud_font.ips \
   ~/.n64patcher/game_fixes/
rm ~/.n64patcher/game_fixes/635A2BFF_sm64_hud_test.bps   # the old 7-site shadow

# then patch and flash as usual; the log line names the fix that ran:
n64patcher clean.z64 --hires -o outdir
```

Read A first (numbers still gone = baseline matches the shipped state),
then B and C: whichever addition brings the numbers back clears its
sites; whichever does not leaves the suspect set halved. Raw image builds
via `makefix.py t3a.z64 1,2,3,4` remain available for direct flashing.

### The geometry is wrong, not (only) the site selection

dataDave's SM64 H2X went the same way from source instead of from the
binary, and its diff names the defect. Verified against
`DavidFallows/sm64`, `master`...`h2x`, the three emitters it touches are
exactly our three suspects — `print.c: render_textrect` (site 0) and
`hud.c: render_hud_tex_lut` / `render_hud_small_tex_lut` (sites 5, 6):

```c
// hud.c  render_hud_tex_lut   (16x16 glyph)
-    ... x << 2, y << 2, (x + 15) << 2, (y + 15) << 2, ..., 4 << 10, 1 << 10);
+    ... (x*2) << 2, y << 2, ((x + 16)*2) << 2, (y + 16) << 2, ..., 1 << 9, 1 << 10);

// hud.c  render_hud_small_tex_lut   (8x8 glyph)
-    ... x << 2, y << 2, (x + 7) << 2, (y + 7) << 2, ..., 4 << 10, 1 << 10);
+    ... (x*2) << 2, y << 2, ((x + 8)*2) << 2, (y + 8) << 2, ..., 1 << 9, 1 << 10);
```

Two things follow.

**1. The end coordinate is off by one tile-edge.** The original writes
`+15` for a 16-pixel tile and `+7` for an 8-pixel one; H2X writes `+16`
and `+8`. Our transform never touches that operand — it only rewrites
the shift, so `(x + 15) << 3` yields `2x + 30` where `2x + 32` is
wanted. Every glyph comes out two pixels narrow. The menus tolerate it
because their glyphs stand apart; the HUD draws its digits edge to edge,
so the error accumulates across the string and the sampling runs off the
tile edge. That matches the symptom exactly: icons survive, numbers do
not. H2X's release note about fixing "an original HUD sprite AA/flicker
issue" is this same `size-1` → `size` correction.

**2. The texture step is not a simple halving.** H2X takes dsdx from
`4 << 10` to `1 << 9` — a factor of 8 — while `makefix.py`'s
`halve_imm()` produces `2 << 10`. The `4 << 10` is bound up with the
tile setup in `dl_hud_img_load_tex_block` and the small variant's
`gDPSetTile`, so the right value cannot be derived from the rectangle
call alone; it has to be measured.

**Do not copy H2X's constants.** H2X renders 640x240 — X doubled, Y
left alone — while our fix rides on SubDrag's 640x480 delta, where both
axes double. Only the `+15` → `+16` correction is geometry-independent
and transfers unchanged.

The older hypothesis is not dead, just demoted: the coordinate field is
masked with `andi rX, rd, 0xFFF`, so `x << 3` overflows once `x` passes
511. That would explain a HUD element positioned from a screen-width
constant the delta already doubled, but it does not explain icons
rendering while digits vanish, which the `+15` does.

Next: rebuild sites 0, 5 and 6 with the end-coordinate operand raised to
the tile size before the shift is doubled, then run the A/B/C bisect
above. The four shipped menu sites depend on the same transform, so
build the HUD correction as its own variant rather than changing
`makefix.py`'s shared path.

## Rebuilding the working files

The ROMs are not in the repository and never will be. Recreate them from a
clean *Super Mario 64 (USA)* dump — SHA-1
`9bef1128717f958171a4afac3ed78ee2bb4e86ce`, CRC1 `635A2BFF`, CRC2
`8B022326`:

```bash
# 1. hires.z64 — Stage 1 output, the base every offset here refers to
python -c "
from n64patcher import n64_core, xdelta_patch
p = n64_core.get_subdrag_patch('635A2BFF', '8B022326')
print(xdelta_patch.apply_xdelta_patch('clean.z64', p, 'hires.z64'))"

# 2. confirm the delta reproduces: 232 regions / 441 bytes
python scripts/sm64_hires/report.py main

# 3. confirm the seven emitters are where this README says
python scripts/sm64_hires/findsites.py

# 4. build
python scripts/sm64_hires/makefix.py hires_fixed.z64
```

All four scripts expect `hires.z64` next to themselves; copy them into the
working directory or copy the ROM in.

## The scripts

- `mipsdis.py` — minimal MIPS III disassembler, enough to read the display
  list builders. Unknown encodings print as raw words rather than guesses.
- `report.py` — diffs clean against hi-res and interprets every changed
  region as a value, as a MIPS immediate and as raw bytes. This is what
  showed the delta touches no 2D code at all.
- `findsites.py` — locates the texture-rectangle emitters by signature:
  `sll rd, rt, 2` feeding an `andi rX, rd, 0xFFF`. Nothing else in this code
  does both, which is what keeps field shifts (`sll rd, rt, 12`) out.
- `makefix.py` — applies the edits and stamps the boot CRC. Every edit
  states the word it expects; a mismatch aborts the build rather than
  writing into whatever happens to be at that offset.
- `make_ips.py` — builds the shippable IPS (sites 1-4) and the bisect
  variants straight from the verified .wip, no ROM required: the project
  BPS encoder emits every changed byte as a literal, and the IPS carries
  exactly those bytes. A structural check aborts the build unless the
  .wip is precisely makefix's 42-word edit plus the CRC restamp.

## Shipping it, once it works

Generate the patch **from the hi-res image to the fixed image**, not from
the clean ROM — Stage 1b applies on top of an already-patched file:

```bash
n64patcher --create-patch hires.z64 hires_fixed.z64 \
  src/n64patcher/game_fixes/635A2BFF_sm64_hud_2x.bps
```

Then a full run should log `Game fix: applied …`, and
`src/n64patcher/game_fixes/README.md` needs its "Known fixes: none yet"
replaced.
