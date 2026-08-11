# SM64 640x480 2D fix — work in progress

Research tooling for the open task described in
[`docs/sm64_hires_patch_analysis.md`](../../docs/sm64_hires_patch_analysis.md):
making Super Mario 64's menus and HUD render correctly on top of the
verified SubDrag 640x480 delta.

**Nothing here is shipped.** `635A2BFF_sm64_hud_textrect_2x.bps.wip` is the
current build, deliberately carrying a `.wip` extension and living outside
`src/n64patcher/game_fixes/` so the Stage 1b lookup cannot pick it up. It
breaks the in-game HUD (see *State* below) and must not reach a release.

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
position, where before the fix they sat half-size in the upper left.

With all seven enabled the in-game HUD loses its **numbers** — the icons
render, the digits do not. One of sites 0, 5, 6 is responsible. Three
bisect builds were prepared for the next hardware run:

```bash
python scripts/sm64_hires/makefix.py t3a.z64 1,2,3,4       # menus only
python scripts/sm64_hires/makefix.py t3b.z64 0,1,2,3,4     # + HUD font
python scripts/sm64_hires/makefix.py t3c.z64 1,2,3,4,5,6   # + HUD LUT
```

The leading hypothesis for the failure is the 12-bit coordinate field. A
value is masked with `andi rX, rd, 0xFFF` after the shift, so `x << 3`
overflows once `x` exceeds 511 pixels. Any HUD element positioned relative
to a screen width the SubDrag delta already doubled to 640 would wrap and
land somewhere else entirely. Checking whether the digit positions are
computed from a width constant, rather than fixed like the icons, is the
first thing to do.

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
