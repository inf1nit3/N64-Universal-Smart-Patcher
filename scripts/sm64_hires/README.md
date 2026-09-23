# SM64 640x480 2D fix — menus shipped, HUD solved in the emulator

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

**HUD:** the cause is found and `make_hud.py` fixes it — full-size HUD,
verified in mupen64plus through the attract demos. Hardware run pending
before it replaces the shipped file. See "The HUD" below.

**Note for this machine:** a user-level fix in
`~/.n64patcher/game_fixes/` overrides the shipped one. A test build left
there during the August hardware runs (`635A2BFF_sm64_hud_test.bps`, all
seven sites) still shadows the menu fix — every `--hires` run of SM64 on
this machine applies the broken HUD edit until it is removed or replaced.

## The approach

RDP rectangle commands carry raw screen coordinates and bypass the
transform pipeline, so no matrix can scale the 2D layer — which is why the
original analysis concluded this needed hundreds of hand-found constants.
What *can* be scaled is the shift that converts a pixel coordinate into the
RDP's 10.2 fixed-point form. The code already does `x << 2`; making it
`x << 3` doubles the coordinate and leaves it in 10.2, at zero instruction
cost. Halving the texture step (dsdx, dtdy) alongside makes the glyph
stretch over twice the pixels instead of repeating.

That works for the menus. It does not work for the HUD, for a reason the
texture step itself gives away — see below.

## State

| Site | ROM offset | Draws | dsdx | Cycle | Status |
|---|---|---|---|---|---|
| 0 | `0x00091BB8` | HUD font: coins, stars, lives, timer | 4.0 | COPY | `make_hud.py`, emulator ✓ |
| 1 | `0x00092D80` | menu glyph | 1.0 | 1-cycle | **hardware ✓, shipped** |
| 2 | `0x00093020` | menu glyph, 2nd form | 1.0 | 1-cycle | **hardware ✓, shipped** |
| 3 | `0x000931A4` | menu glyph, 3rd form | 1.0 | 1-cycle | **hardware ✓, shipped** |
| 4 | `0x00093520` | menu glyph, 4th form | 1.0 | 1-cycle | **hardware ✓, shipped** |
| 5 | `0x0009DDB4` | HUD LUT 16x16: digits, icons | 4.0 | COPY | `make_hud.py`, emulator ✓ |
| 6 | `0x0009E010` | HUD LUT 8x8: small glyphs | 4.0 | COPY | `make_hud.py`, emulator ✓ |

Sites 1–4 were confirmed on a SummerCart64 on 2026-08-11 by photographing
the file-select screen and the Peach letter.

## The HUD

### Why it broke: COPY mode

With all seven sites through `makefix.py` the HUD lost its numbers. The
column that explains it is dsdx. The four menu emitters step the texture
by 1.0 per pixel; the three HUD emitters step it by **4.0**. That is the
signature of the RDP's COPY cycle type, and segment 2 confirms it: the
list the HUD calls first (`dl_hud_img_begin` in the decomp, segment
offset `0x11AC0` in the US ROM) sets `G_CYC_COPY`.

Copy mode moves four texels per clock and **cannot scale**. dsdx must be
exactly 4.0, and the rectangle's end coordinate is *inclusive* — which is
why the code writes `x + 15` for a 16-pixel glyph. `makefix.py` halves the
step to 2.0 and doubles the shift; in copy mode that is simply invalid.

This corrects the entry made here on 2026-09-16, which blamed the `+15`
as an off-by-one. It is not one: `+15` is right for an inclusive end.
H2X changes it to `+16` because H2X also changes the cycle type — in the
1-cycle pipeline the end is exclusive. H2X's `bin/segment2.c` diff shows
the switch: `G_CYC_COPY` → `G_CYC_1CYCLE`, plus a combiner
(`G_CC_DECALRGBA`), point filtering and a new render mode
(`G_RM_TEX_EDGE`), with the matching restores in `dl_hud_img_end`.

### Two fixes, both in `make_hud.py`

**`--mode full`** — H2X's route, adapted to 640x480. The HUD moves to the
1-cycle pipeline, where the RDP can scale: start `x << 3`, end
`(2x + 2*size) << 2` (exclusive), dsdx and dtdy 0.5. 24 code words across
the three emitters, plus both display lists rewritten.

The lists are the hard part. They sit in segment 2, which is
MIO0-compressed, and H2X *adds* two commands to each — impossible in
place, because every later segment-2 address would move. The fix merges
instead: cycle type (bits 20-21), texture perspective (19) and texture
filter (12-13) all live in `SETOTHERMODE_H`, so one command with shift
12, length 10 sets all three. That frees exactly the two slots the
combiner needs; the LUT/LOD/detail fields between them are set to their
defaults, which is what the HUD's RGBA16 textures use. The combiner and
render-mode words are copied out of H2X's compiled ROM rather than
derived. Recompressed with crunch64, segment 2 comes out at 48 392 bytes
against a 48 400-byte slot.

**`--mode copy`** — the fallback. Stays in copy mode, doubles only the
position: glyphs land in the right place at half size, with gaps between
them ("1 5" for 15). 18 code words, no display-list changes, nothing that
could break the way the first attempt did.

Both rewrite each end coordinate's `addiu t, x, size-1 ; sll d, t, 2`
pair into `sll d, x, 3 ; addiu d, d, k` in the same two slots. The
original temporary `t` is no longer written, so the builder checks
mechanically that nothing reads it before it is overwritten.

### Emulator result (mupen64plus 2.6.0, attract demos, frames 900–4500)

| State | HUD |
|---|---|
| shipped (menus only) | half size, all of it crowded into the left half |
| `--mode copy` | right positions across the width, half size, letter-spaced |
| `--mode full` | right positions, **full size, compact** — looks like the original at 2x |

In `--mode full` all three emitter types render correctly: the counters
(16x16), the camera-mode arrow (8x8) and the "PRESS START" line (text).
Both IPS files reproduce the test ROMs byte-for-byte through the real
pipeline (`clean.z64 --hires` with the IPS in the user fix folder).

### Hardware run

```bash
python scripts/sm64_hires/make_hud.py --mode full --check work/sm64/hires.z64 \
    --ips scripts/sm64_hires/bisect/635A2BFF_hud_full.ips
python scripts/sm64_hires/make_hud.py --mode copy --check work/sm64/hires.z64 \
    --ips scripts/sm64_hires/bisect/635A2BFF_hud_copy.ips

# one at a time, replacing whatever is in the user folder:
rm ~/.n64patcher/game_fixes/635A2BFF_*
cp scripts/sm64_hires/bisect/635A2BFF_hud_full.ips ~/.n64patcher/game_fixes/
n64patcher clean.z64 --hires -o outdir      # log: Game fix: applied 635A2BFF_hud_full.ips
```

Each IPS carries the shipped menu fix too, so a run exercises menus and
HUD together. Look at: the counters during a level (lives, coins, stars),
the camera icon bottom-right, the timer in a race, the power meter after
taking damage, a dialog box, the pause screen. If `full` is clean, it
replaces `635A2BFF_sm64_menu_2x.ips`. If `full` shows anything wrong,
`copy` is the safe fallback to ship instead.

Unlike the menu fix, these IPS files cannot be rebuilt without the ROM:
the liveness scan and the segment-2 recompression both read
`hires.z64`.

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
  .wip is precisely makefix's 42-word edit plus the CRC restamp. Its
  `--bisect` variants belong to the August site bisect and are obsolete:
  all three HUD sites fail for the same reason.
- `make_hud.py` — the HUD fix, `--mode full` or `--mode copy` (see "The
  HUD"). Checks every expected word, runs the liveness scan, rewrites
  and recompresses segment 2 in full mode, and emits a test ROM or an
  IPS that includes the shipped menu fix.

## Shipping the HUD, once hardware confirms it

Replace the shipped file with the confirmed IPS, keeping exactly one
`635A2BFF_*` file in `game_fixes/` — the lookup is keyed on CRC1:

```bash
git rm src/n64patcher/game_fixes/635A2BFF_sm64_menu_2x.ips
cp scripts/sm64_hires/bisect/635A2BFF_hud_full.ips \
   src/n64patcher/game_fixes/635A2BFF_sm64_2d_2x.ips
```

Then update `src/n64patcher/game_fixes/README.md`, `tests/test_sm64_menu_fix.py`
(whose byte-range checks assume menus only) and `make_hud.py`'s `MENU_IPS`
path, and note in the changelog that the shipped file can no longer be
rebuilt without a ROM.
