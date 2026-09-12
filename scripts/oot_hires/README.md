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
  frozen video state for hi-res + non-interlaced), `vitables`
  (VI-tables-only diagnostic), `640p` (**the working route**), and
  `640pdbg` (640p + title auto-advance for unattended emulator runs).
- `sites.txt` — the scan output that pinned the anchors.
- `recipe.oot-hires-exp.json` + `zelda-oot-usa-rev0-640x480i-exp.bps` —
  the user-installable pair: JSON into `~/.n64patcher/patches/`, BPS
  next to it. `--hires` then applies it for this dump.
- `cand_oot_*.z64` — built candidates (gitignored, ROMs never enter the
  repository).

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
- note: ovl_file_choose recompresses to 36374 bytes against a 36384-byte
  ROM slot — only 10 bytes of headroom, so debug edits there must stay
  minimal (a .data table word compresses smaller than code edits)

Known risk: gZBuffer stays 320x240, so a 640-wide scissor overflows
0x25800 bytes into gGfxSPTaskOutputBuffer (survived in the emulator;
hardware verdict pending). HUD/menus keep the 320-space layout where
their positions are compile-time constants — the per-renderer 2D pass
is future work (the title/File Select screens already derive their
positions from the runtime viewport and land correctly).

## The 2D pass (HUD scaling) — slices 1+2 work

The HUD's x-positions live in REG editor entries assigned by
Regs_InitDataImpl / Interface_Init as `li rt, value` ... `sh rt, off`
pairs — the sh offset pins down the register. Verified with capstone
against the decomp: the runtime reg-data pointer is gRegEditor->data
+ 0x14, so true store offsets are the regs.h byte offsets + 0x14
(e.g. R_C_UP_BTN_X=254 stores at 0x810, R_ITEM_BTN_X(1)=227 at 0x822).
`makeoot.py`'s patch_hud_scale doubles the li immediates feeding those
stores, pairing strictly by the sh rt field.

Slice 1+2 (14 li sites): B/C button, item icon, ammo, A button, C-up,
start and magic-meter x positions — verified in the emulator: the C
buttons land on the right side of the 640-wide screen, gameplay stable.
Two entries (R_ITEM_BTN_X(0) 160 and R_START_BTN_X 132) need
hand-pinned extra sites (their li serves two stores or sits far from
the store): code+0xD0EEC and code+0xD14EC.

Slice 3 (open): a few registers still unmatched (R_ITEM_AMMO_X(2),
R_C_UP_ICON_X, R_MAGIC_METER_X - different codegen, warnings printed)
plus hearts/rupee counters drawing from z_parameter.c hardcoded
vertices. Dialog textboxes use R_TEXTBOX_* (same mechanism).

Slot note: the recompressed code segment only just fits (634960
bytes); the HUD edits cost ~11, so the 640p flavors zero 0x40 bytes
of the dead-in-US JP message table (code+0xFF8AC) as a compression
donation. Keep an eye on headroom for further edits.

`480i` (the ViMode-editor route) renders the 3D scene full-screen
640x480i, but interlaced output is rejected on this setup (hardware
verdict: unusable) — kept for reference only.

## Status

- **640p**: emulator-verified through logo -> title -> File Select
  (frames 120/900/1000-2000), progressive, no interlace. NOT yet on
  real hardware — SummerCart64 run pending before bundling.
- **HUD / dialogs**: compile-time 320 positions stay upper-left; the
  per-renderer 2D pass over the decomp map is future work. Title and
  File Select already land correctly (runtime-viewport positions).
- The Stage 1b game-fix mechanism deliberately does NOT fire for BPS
  builds (fixes are built against the SubDrag image).

## Rebuilding

Requires `clean.z64` (big-endian US Rev 0 dump, symlinked or copied
here) and the n64patcher package on `sys.path` (for the CRC engine):

    python makeoot.py            # reads flavor.txt, writes candidate to stdout

`flavor.txt` holds one of: `480i`, `240p`, `vitables`, `640p`,
`640pdbg`. Emulator test loop:

    python makeoot.py > cand.z64
    mupen64plus --testshots 120,900 --sshotdir /tmp/shots cand.z64
    # 640pdbg for the unattended run into File Select:
    echo 640pdbg > flavor.txt && python makeoot.py > cand_dbg.z64
    mupen64plus --testshots 800,1100,1500 --sshotdir /tmp/shots cand_dbg.z64
