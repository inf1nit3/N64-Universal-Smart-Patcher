# oot_hires — Zelda: Ocarina of Time (USA) Rev 0, 640x480i (EXPERIMENTAL)

Development workspace for the in-project OoT hi-res build. The patch
enables the game's own runtime-width machinery (found via the
zeldaret/oot decompilation, which targets this exact revision) and moves
the framebuffer pair into the Expansion Pak area. Verified in mupen64plus
at 640x480i: 3D renders full-screen with no corruption. Menus/HUD keep
the original 320-space layout (upper-left quadrant) for now — the same
known limitation the SubDrag patches ship with.

## Files

- `findsites.py` — scans the ROM for the compiled store patterns of
  ViMode_Init / SysCfb_Init / View_Init (reads `clean.z64` here).
- `makeoot.py` — builds a candidate from `clean.z64` (Yaz0-decompress
  code segment, expected-word edits, recompress, splice, CRC restamp).
  The flavor comes from `flavor.txt`: `480i` (shipped candidate), `240p`
  (not functional yet — Nintendo's Configure math produces a frozen video
  state for hi-res + non-interlaced), `vitables` (VI-tables-only
  diagnostic).
- `sites.txt` — the scan output that pinned the anchors.
- `recipe.oot-hires-exp.json` + `zelda-oot-usa-rev0-640x480i-exp.bps` —
  the user-installable pair: JSON into `~/.n64patcher/patches/`, BPS
  next to it. `--hires` then applies it for this dump.
- `cand_oot_*.z64` — built candidates (gitignored, ROMs never enter the
  repository).

## The 640-wide progressive route (640p flavor) — status: left-half

`640p` patches the boot VI tables to a 640-wide progressive mode
(xScale 1:1, origin 1280), widens `gScreenWidth` (.data AND Main()'s
boot assignment — both matter, Main re-assigns at boot), the View
viewport and the SysCfb framebuffers (640x240, same total bytes as
stock — fits a 4 MB console). Emulator result after all that: the 3D
scene still renders only across the left 320 columns — more 320-space
sites (title draws, HUD, scissor paths outside gScreenWidth) exist than
the macro grep reveals. Closing this gap is the per-renderer 2D pass
described below; do not ship `640p` in this state.

`480i` (the ViMode-editor route) renders the 3D scene full-screen
640x480i, but interlaced output on a CRT is flicker-heavy — hardware
verdict pending; per-renderer 2D pass needed either way.

## Status

- **480i**: emulator-verified (mupen64plus, title + attract scenes
  full-screen, stable across 4800+ frames). NOT yet verified on real
  hardware — do not bundle until a SummerCart64 run confirms it.
- **HUD / File Select / dialogs**: render with the original 320-space
  positions (upper-left quadrant). The name-entry screen scales wide
  (its positions derive from runtime screen dimensions). A per-renderer
  2D pass over the decomp map is future work.
- The Stage 1b game-fix mechanism deliberately does NOT fire for BPS
  builds (fixes are built against the SubDrag image).

## Rebuilding

Requires `clean.z64` (big-endian US Rev 0 dump, symlinked or copied
here) and the n64patcher package on `sys.path` (for the CRC engine):

    python makeoot.py            # reads flavor.txt, writes cand_oot.z64
