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

## Emulator pre-screen (2026-09-24) — read before flashing anything

Two things learned on SM64's HUD change how these variants must be read.

**COPY mode.** An emitter that steps its texture by 4.0 (`lui r, 0x1000 ;
ori r, r, 0x0400`) draws in the RDP's COPY cycle type, which cannot
scale. Doubling its shift puts a 1:1 glyph into a 2x rectangle; halving
its step is invalid outright. That broke SM64's HUD (see
`scripts/sm64_hires/README.md`, "The HUD"). `makefix2d.py` now checks
every requested site's enclosing function for that step word and
**refuses** to build if it finds one; `--skip-copy-mode` builds the rest
and lists what it left out, `--allow-copy-mode` forces it. On SM64 the
check separates the three HUD emitters from the four menu emitters
without a miss. `analyze.py` reports the count up front.

| Game | Sites in COPY-mode functions |
|---|---|
| GoldenEye | 0 of 33 |
| Quake II | 0 of 25 |
| Forsaken | 0 of 1 |
| **F-Zero X** | **46 of 169** — blitter 30/88, `reg_00B` 12/15, `reg_00A` 4/12; 13 of 55 steps |

The F-Zero variants in `bisect/` and `work/B30E…/bisect/` are rebuilt
with `--skip-copy-mode` (A 58 words, B 80, C 123, D 165).

**mupen64plus shows breakage the variants were built to find.** Running
each variant through boot, intro and attract mode (frames 300–4800,
8 MB) against its unmodified hi-res image:

| Variant | Emulator verdict |
|---|---|
| F-Zero X C, D (COPY sites skipped) | title art torn into colour streaks — the signature of a doubled tile *size*, so texture-space packers (SETTILESIZE uses the same `<< 2 / & 0xFFF` idiom) are in the groups; pilot portraits turn into white boxes |
| GoldenEye A, C | intro identical to the base image — neither better nor worse; menus and HUD are not reachable without input |
| GoldenEye D (and so B's steps) | breaks the intro credits, which the base image already renders correctly: glyphs stretched 2x vertically and cut in half |
| Quake II A | legal text smeared into a solid block, title menu reduced to green blocks |
| Quake II B | the same, plus distorted 3D geometry — some "2D" packers are shared with geometry code |

What is worth a hardware round: **GoldenEye A or C**, to look at the
file select, the watch and the in-game HUD. Nothing else. F-Zero X and
Quake II need their site tables re-cut first — separating screen-space
packers from texture-space and geometry ones — and GoldenEye's step
halving is wrong for at least its credit text. The August claim that
GoldenEye's whole 2D layer sits in 320-space also does not survive: the
Enhanced delta already renders the credits at 640-space size.

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
