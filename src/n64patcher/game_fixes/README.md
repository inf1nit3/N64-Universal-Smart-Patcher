# game_fixes — Per-game menu/HUD fix patches

This folder holds optional IPS/BPS patches that fix known 2D (menu / HUD)
rendering problems on top of a verified SubDrag hi-res `.xdelta`.

Background: the hi-res deltas change the VI mode, framebuffers and viewport
plumbing to 640×480, but many games draw their menus and HUD with
**absolute 320×240 pixel coordinates** (SM64: `gSPTextureRectangle` /
`gDPFillRectangle` in `print.c`, `hud.c`, `ingame_menu.c`). Those elements
stay stuck in the top-left corner at half size in a 640×480 framebuffer.
See `docs/sm64_hires_patch_analysis.md` for the full breakdown.

## Naming & lookup

Patches are matched by the ROM's **CRC1** (8 uppercase hex digits), so a fix
can never reach a different revision of the same game:

    <CRC1>_<short-description>.ips|.bps

Example (Super Mario 64 (USA) = CRC1 `635A2BFF`):

    635A2BFF_sm64_menu_hud_fix.ips

Two directories are searched, later wins:

1. this folder — shipped with the tool
2. `~/.n64patcher/game_fixes/` — per-user, survives reinstalls

A value that does not name a CRC1 matches nothing at all. That is
deliberate: an IPS carries no source checksum, so a fix handed to the wrong
dump would apply cleanly and corrupt it in silence.

The stage is opt-in per patch file: it runs only when a matching file
exists, and only after the `.xdelta` stage actually applied.

## Adding a fix

1. Build the base image: apply the SubDrag `.xdelta` to the clean ROM (the
   tool does this with `--hires` / the Modern 4K preset).
2. Tune the menu/HUD constants on that image until they look right on
   hardware or in an emulator.
3. Generate a linear patch **from the hi-res image to your fixed image** —
   not from the clean ROM. The stage applies the fix on top of the
   already-patched file, so that is the base its offsets must match. Drop
   the result here under the CRC1 name of the **clean** dump, which is what
   the tool reads from the ROM header before patching.
4. Run `pytest tests/test_n64_core.py -k GameFix` and check a real run's log
   for the `Game fix:` line.

## Known fixes

- `635A2BFF_sm64_menu_2x.ips` — Super Mario 64 (USA), menu glyph scaling
  for the SubDrag 640x480 delta. Four texture-rectangle emitters (the
  file-select labels and dialog text): each coordinate shift `x << 2`
  becomes `x << 3` and each texture step is halved, 24 changed bytes in
  total. Confirmed on a SummerCart64 (file-select and the Peach letter
  land at full size and position). The three HUD emitters are NOT
  included: with them enabled the HUD loses its numbers, and the bisect
  that decides which site is responsible is still open — see
  `scripts/sm64_hires/README.md`. Rebuilt from the verified working
  patch by `scripts/sm64_hires/make_ips.py`.
