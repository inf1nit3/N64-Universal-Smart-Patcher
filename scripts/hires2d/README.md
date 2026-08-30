# hires2d — per-game 2D fixes for verified 640x480 deltas

The generic sibling of `scripts/sm64_hires/`: the analysis that produced
the shipped SM64 menu fix, lifted from one engine to a survey any game
can run. The recipe is the one SM64 proved on hardware — the 2D layer
draws through RDP rectangle commands whose screen coordinates are packed
as `x << 2` into s10.2 fixed point, so doubling the shift doubles the
coordinate at zero instruction cost, and halving the s5.10 texture steps
stretches glyphs instead of repeating them.

## The tools

- `diffreport.py` — classify a clean → hi-res delta (works word-for-word
  where the delta is a minimal VI patch; tells you when it is not).
- `find2d.py` — survey a **hi-res image**: the `sll rd, rt, 2 → andi
  0xFFF` coordinate packers (the SM64 signature) and the static
  texture-rectangle commands with their coordinate spaces.
- `makefix2d.py` — apply the fix from a site file: every edit states the
  word it expects, a mismatch aborts, boot checksums are restamped, and
  `--ips` emits an installable Stage 1b patch.
- `<game>_sites.py` — the per-game data: coordinate sites grouped by
  function, step immediates listed separately.

## GoldenEye 007 (USA) — analysis complete, awaiting hardware

The delta (`GE640x480iEnhanced[SubDragTrevorZoinkity]`) is a
restructured Enhanced build — 31,060 changed runs, so byte-diffing is
meaningless — but the survey on the hi-res image found **33 coordinate
packers, every one byte-identical to the clean ROM**: the delta leaves
the whole 2D layer in 320-space, exactly the structural gap SM64 had.
The dense cluster at `0xE2BB4..0xE31BC` is the glyph blitter
(SETTILESIZE `0xE5`, TEXRECT `0xB4`, TEXRECTFLIP `0xB3`, steps
`0x0400`/`0xFC00`).

Nothing is shipped for GoldenEye. Four bisect variants are prepared:

| Variant | Sites | Words | What the flash decides |
|---|---|---|---|
| `A_blitter` | blitter only | 19 | do menus/watch text scale? |
| `B_blitter_steps` | + step halving | 30 | do glyphs stretch, not repeat? |
| `C_all` | all 33 coord sites | 33 | did the outer groups need touching? |
| `D_all_steps` | all + steps | 44 | the works |

Flashable images live in `work/ge/bisect/` (untracked, built by
`makefix2d.py`); the same edits as Stage 1b IPS files are in
`bisect/DCBC50D1_ge_bisect_*.ips` for install-into-`~/.n64patcher/
game_fixes/` runs.

**Protocol:** flash in order A → B → C → D. After each, check the file
select, the in-game HUD and the watch screen. A group that corrupts
textures (rather than mis-positioning) contains texture-space sites and
must be re-cut before it can ship. Report which variant first looks
right; the shipped fix is then rebuilt from that group selection, and
the SM64 route (`make_ips.py`-style byte-exact checks + tests) turns it
into a database entry.

## Survey across the verified recipes (2026-08-30)

Every verified dump analysed with the same flow (`analyze.py` on the
user-supplied clean dumps):

| Game | Packers | Left 320-space by the delta | Verdict |
|---|---|---|---|
| Super Mario 64 (USA) | 7 | all 7 | menu fix **shipped** (sites 1-4); end-to-end validated: the local pipeline reproduces the hardware-tested base image byte-exactly (.wip source CRC32 `D85A1129`) and the shipped IPS reproduces all 24 verified bytes |
| GoldenEye 007 (USA) | 33 | all 33 | bisect variants ready (below) |
| F-Zero X (USA) | 169 | all 169 | bisect variants ready; `r_00Cxxx` (88 sites) is the presumed shared blitter |
| Quake II (USA) | 25 | all 25 | bisect variants ready (`A_all`, `B_all_steps`) |
| Forsaken 64 (USA) | 1 | the one | single site on record; HUD likely uses another idiom |
| Golden Nugget 64 (USA) | 0 | - | the delta covers the 2D layer differently; nothing for this method |
| Banjo-Kazooie (USA Rev A) | - | - | blocked: the available dump is v1.0 (`A4BF9306`), the recipe needs Rev A (`CD7559AC`) |
| Pokemon Snap (USA) | - | - | no retail dump in the collection |

Flashable images live under `work/<key>/bisect/`; the same edits as
Stage 1b IPS files (install into `~/.n64patcher/game_fixes/`) are in
`bisect/`. Protocol as below - per game, in order, watching for text
scale on menus/HUD and for texture corruption (which means the group
contains texture-space sites and must be re-cut).

## Rebuilding for another game

1. `n64patcher <clean dump> --hires -o outdir` (or apply the recipe's
   delta directly) to get the hi-res image.
2. `python find2d.py <hi-res image>` — check the packers are unchanged
   from the clean ROM (the game needs a fix at all).
3. Write `<game>_sites.py`: group the packers by code region, list the
   step immediates (`ori/lui` with `0x0400`/`0x0800`/`0xFC00`-class
   values near the blitter).
4. `makefix2d.py --group … --steps …` → bisect variants → hardware.

ROMs never enter the repository; only offsets and words do.
