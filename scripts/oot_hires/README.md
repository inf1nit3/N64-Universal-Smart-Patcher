# oot_hires — Zelda: Ocarina of Time (USA) Rev 0, 640-wide hi-res

Development workspace for the in-project OoT hi-res build. The main
route is the **640p flavor**: 640x240 PROGRESSIVE via the game's own
static VI tables (found via the zeldaret/oot decompilation, which
targets this exact revision) — no interlace, which is unusable on this
setup. Verified in mupen64plus through the whole boot chain (logo ->
title -> File Select) with the 3D scene full-width. HUD/dialogs keep
the original 320-space layout where their positions are compile-time
constants (per-renderer 2D pass = future work, same known limitation
the SubDrag patches ship with).

## Files

- `findsites.py` — scans the ROM for the compiled store patterns of
  ViMode_Init / SysCfb_Init / View_Init (reads `clean.z64` here).
- `makeoot.py` — builds a candidate from `clean.z64` (Yaz0-decompress
  code segment, expected-word edits, recompress, splice, CRC restamp),
  written to STDOUT; logs go to stderr. The flavor comes from
  `flavor.txt`: `480i` (interlaced reference route, rejected),
  `240p` (not functional yet — Nintendo's Configure math produces a
  frozen video state for hi-res + non-interlaced), `240pdbg` (240p +
  auto-chain; hardware bisect rung: standard 320-signal, fits 4MB),
  `vitables`
  (VI-tables-only diagnostic), `640p` (**the working route**), and
  `640pdbg` (640p + title auto-advance for unattended emulator runs).
  `--zrel` (with `640p`/`640pdbg`) relocates gZBuffer — see below.
- `sites.txt` — the scan output that pinned the anchors.
- `recipe.oot-hires-exp.json` + `zelda-oot-usa-rev0-640x240p-exp.bps` —
  the user-installable pair: JSON into `~/.n64patcher/patches/`, BPS
  next to it (an operation's file is resolved against the recipe's own
  directory first). `--h2x` then applies it for this dump — the recipe
  carries `flavor: 640x240`, so `--hires` alone does not pick it up.
  The BPS is `640p --zrel` (see "Round 3 candidate" below).
- `cand_oot_*.z64` — built candidates (gitignored, ROMs never enter the
  repository).

**Trap when picking a candidate to ship from:** the debug edits live in
`ovl_file_choose`, which sits past the first megabyte and therefore
outside the CIC-6102 checksum window — a dbg build and a clean build
carry the *same* CRC pair. Comparing CRCs does not tell them apart; a
byte-compare of ROM 0x0B099E0..0x0B12800 against `clean.z64` does.

## The 640-wide progressive route (640p flavor) — status: WORKS

`640p` patches the boot VI tables (osViModeNtscLan1/MpalLan1) to a
640-wide progressive mode (xScale 1:1, origin 1280) — on NTSC 1.0 the
scheduler never re-issues `osViSetMode` (the per-frame `viMode` pointer
stays NULL), so those static tables drive the display for the whole
session. The game renders 640-wide because `gScreenWidth` is widened
both in its `.data` initializer and in Main()'s boot assignment; the
only other runtime writer of `gScreenWidth` is ViMode_Update, which is
dead code unless the SREG VI editor is on (its store is NOPed anyway).
SysCfb's framebuffer pair grows to 2x 640x240 and the 8MB fb-end moves
to 0x80600000 so the game heap keeps its stock size — **the Expansion
Pak is required** (on 4 MB the console would take the 4MB branch and
the shrunken heap is likely to starve the game).

Key sites (all asserted word-for-word, anchors resolved against the
zeldaret/oot ntsc-1.0 map):

- boot VI tables at ROM 0x6FC0/0x7010 (width/xScale/origins)
- SysCfb_Init fb-offset constants + the 8MB fb-end lui
- View_Init viewport rightX — the `li 240/li 320` pair appears TWICE in
  the code segment (View_Init and func_800A994-area code), so the anchor
  is View_Init's unique 4-word window; the first-hit anchor used earlier
  patched the WRONG site and left the 3D in the left half
- gScreenWidth `.data` + Main() boot assignment
- ViMode_Update's width/height copy -> NOP

Emulator verification (mupen64plus 2.6.0, `--testshots`, 8 MB):

- frame 120: N64 logo, frame 900: title screen (3D scene full-width)
- `640pdbg` (title START checks forced true, andi->ori) auto-advances:
  frames 1000-2000 show File Select full-width and stable
- `640pdbg` also swaps the File Select name-entry update func for
  FileSelect_LoadGame (one .data word in sFileSelectUpdateFuncs[]):
  the unattended run opens File 1 with the default save and lands in
  gameplay — frames 3000..16000 show Link's house full-width 640x240p,
  stable, no corruption. HUD elements render in their 320-space
  positions (left half, full height) as documented above.
- no interlace anywhere (progressive 640x240p)

## gZBuffer relocation (`--zrel`) — status: first hardware verdict

The 2026-09-13 hardware round returned "black screen, no boot" for the
plain `640pdbg` candidate (CRCs verified fine). The builder comment had
flagged the cause: the 640-wide scissor makes the RDP write 0x4B000
bytes of depth into the stock 320x240 z-buffer (0x25800 bytes at
0x8012BE40, `gGfxSPTaskOutputBuffer` follows directly at 0x80151640) —
0x25800 bytes of overflow into the SPTask output buffer and beyond.
mupen survives that; real hardware does not.

`--zrel` moves gZBuffer to 0x8056A000: fb0's constant is lowered to
end-0xE1000 (the heap ends at fb0, so the region below fb0 stays
heap-free), and the z-buffer sits in the resulting 0x4B000 gap between
the fb0 image end (0x8056A000) and fb1 (0x805B5000). All 10 reference
sites in the code segment are rewritten (`lui %hi` + `addiu/ori %lo`
pairs and the `lhu %lo(rX)` load-offset form in
Environment_GetPixelDepth), each site pinned against the decomp ELF
symbol table (see ZBUF_EXPECT_SITES in makeoot.py — the builder refuses
to build if the site list drifts). Known cosmetic leftovers: the CPU
depth readers keep their 320 stride (sun glare/glow falloff sample
wrong pixels), and the pause-menu prerender saves 320-wide into the
wide framebuffer (squeezed pause background).

Emulator re-verification after the relocation: `640pdbg --zrel`
auto-chains into gameplay (Link's treehouse) with clean depth, HUD
slices intact — /tmp/shots_zrel, frames 200..2400.

Also fixed while rebuilding the ladder: the ViMode_Init store offsets
(vi_state/vi_lores/vi_moden/vi_w/vi_h) were stale after an earlier
anchor refinement — 480i/240p flavors asserted-mismatched on rebuild.
All five offsets re-derived word-for-word; `240pdbg` (auto-chain on the
standard 320 signal, fits 4MB) now builds for the hardware bisect
ladder documented in scripts/hi-res-hardware-testplan.md.

## Hardware round 2 (2026-09-13): baseline ✓, 240pdbg ✓, 640p picture destroyed

The bisect ladder worked: cart/console fine (baseline), all gameplay
patches fine (240pdbg), and the 640p failure isolated to something
640-specific. The conclusion drawn at the time — that `VI_X_SCALE` was
the culprit and had to drop from `0x400` to `0x100` — was **wrong**, and
commit e135705 is reverted.

Nintendo's own source settles the encoding. `viint.h`:

```c
#define SCALE(scaleup, off) (F210(1.0f / (f32)(scaleup)) | (F210((f32)(off)) << 16))
```

The register holds the **reciprocal** of the upscale factor, not the
factor: the stock table's `SCALE(2, 0)` is `F210(0.5)` = `0x200` for a
320-wide framebuffer. `z_vimode.c:175` writes the same thing as a
formula, and it is the one `ViMode_Configure` uses to build MM's
hi-res notebook mode:

```c
xScale = (width << 10) / (SCREEN_WIDTH * 2 + rightAdjust - leftAdjust);
```

`SCREEN_WIDTH` is 320, so the divisor is the 640-pixel active window.
Check it against the stock table: `(320 << 10) / 640 = 0x200` ✓.
For a 640-wide framebuffer: `(640 << 10) / 640 = **0x400**`.

`0x100` encodes a 160-wide framebuffer — the VI shows the left quarter
at 4x. That is visible even in mupen, whose VI models the scaler only
roughly: with `0x100` the boot wordmark is cropped at the
right edge and the title screen comes out magnified and cut off; with
`0x400` the full picture is there.

Which leaves round 2's destroyed picture unexplained by the VI tables —
and the candidate it tested was built **before** `--zrel`. The 640-wide
scissor writing 0x4B000 bytes of depth into a 0x25800-byte buffer, over
`gGfxSPTaskOutputBuffer`, is a far better fit for "right and bottom
garbage, unstable" than a scan-rate error would be. Round 3 tests that.

- note: ovl_file_choose recompresses to 36374 bytes against a 36384-byte
  ROM slot — only 10 bytes of headroom, so debug edits there must stay
  minimal (a .data table word compresses smaller than code edits)

HUD/menus keep the 320-space layout where their positions are
compile-time constants — the per-renderer 2D pass is future work (the
title/File Select screens already derive their positions from the
runtime viewport and land correctly).

### Round 3 candidate (2026-09-17, rebuilt, not yet on hardware)

`zelda-oot-usa-rev0-640x240p-exp.bps` was still the 2026-09-12 build,
without `--zrel`. Rebuilt from `640p --zrel` against the current
`makeoot.py`:

    clean.z64      CRC1 EC7011B7  CRC2 7616D72B  CRC32 CD16C529
    640p --zrel    CRC1 EC700137  CRC2 75F8FA0E  CRC32 00C7EB1A

The CRC pair is the same one the recipe declared before the xScale
detour, which is the expected consequence of reverting it: the VI
tables sit inside the CIC window, the zrel edits do not.

Verified before shipping: VI tables read `0x280`/`0x400`, all 10 zrel
sites rewritten, the BPS round-trips byte-identically to the candidate,
`ovl_file_choose` matches `clean.z64` (no debug edits), a full `--h2x`
pipeline run reproduces the candidate byte-for-byte, and mupen renders
logo → title with the whole picture present and the 2D layer in its
320-space upper-left position.

## The 2D pass (HUD scaling) — slices 1+2 work

The HUD's x-positions live in REG editor entries assigned by
Regs_InitDataImpl / Interface_Init as `li rt, value` ... `sh rt, off`
pairs — the sh offset pins down the register. Verified with capstone
against the decomp: the runtime reg-data pointer is gRegEditor->data
+ 0x14, so true store offsets are the regs.h byte offsets + 0x14
(e.g. R_C_UP_BTN_X=254 stores at 0x810, R_ITEM_BTN_X(1)=227 at 0x822).
`makeoot.py`'s patch_hud_scale doubles the li immediates feeding those
stores, pairing strictly by the sh rt field.

Slice 1+2+3 (15 li sites): B/C button, item icon, ammo, A button,
C-up, start and magic-meter x positions — verified in the emulator:
the C buttons land on the right side of the 640-wide screen, gameplay
stable. Three entries need hand-pinned extra sites (li serves two
stores, sits far from the store, or the value register is shared):
R_ITEM_BTN_X(0) (code+0xD0EEC), R_START_BTN_X (code+0xD14EC) and
R_ITEM_AMMO_X(2) (code+0xD1C40).

Slice 4 (open): R_C_UP_ICON_X and R_MAGIC_METER_X are fed from shared
constant registers (s1=18 serves several y-positions too), so naive
value scaling would corrupt neighbours — they need per-consumer patch
sites. Hearts/rupee counters draw through Health_DrawMeter's float
literal pool (30.0f/10.0f/-130.0f x-positions) — the pool lives in
.rodata and is shared, so scaling means either pool splitting or a
matrix-scale injection. Dialog textboxes use R_TEXTBOX_* (mechanism
proven, sites not yet enumerated).

Slot note: the recompressed code segment only just fits (634960
bytes); the HUD edits cost ~11, so the 640p flavors zero 0x40 bytes
of the dead-in-US JP message table (code+0xFF8AC) as a compression
donation. Keep an eye on headroom for further edits.

`480i` (the ViMode-editor route) renders the 3D scene full-screen
640x480i, but interlaced output is rejected on this setup (hardware
verdict: unusable) — kept for reference only.

## Status

- **640p**: emulator-verified through logo -> title -> File Select ->
  gameplay, progressive, no interlace. On hardware, round 2 rejected
  the then-current build (xScale `0x400`); the cause is understood and
  fixed, but the corrected build has **not been on a console yet** —
  that is the open item, see "Round 3 candidate" above.
- **HUD / dialogs**: compile-time 320 positions stay upper-left; the
  per-renderer 2D pass over the decomp map is future work. Title and
  File Select already land correctly (runtime-viewport positions).
- The Stage 1b game-fix mechanism deliberately does NOT fire for BPS
  builds (fixes are built against the SubDrag image).

## Rebuilding

Requires `clean.z64` (big-endian US Rev 0 dump, symlinked or copied
here) and the n64patcher package on `sys.path` (for the CRC engine):

    python makeoot.py            # reads flavor.txt, writes candidate to stdout

`flavor.txt` holds one of: `480i`, `240p`, `240pdbg`, `vitables`,
`640p`, `640pdbg`; `--zrel` is an extra argument for the two `640p`
flavors. The shipped BPS is built with `640p` + `--zrel`. Note that
`flavor.txt` is read from the **current** directory, so run the script
from this folder — starting it from the repository root drops a stray
`flavor.txt` there. Emulator test loop:

    python makeoot.py > cand.z64
    mupen64plus --testshots 120,900 --sshotdir /tmp/shots cand.z64
    # 640pdbg for the unattended run into File Select:
    echo 640pdbg > flavor.txt && python makeoot.py > cand_dbg.z64
    mupen64plus --testshots 800,1100,1500 --sshotdir /tmp/shots cand_dbg.z64
