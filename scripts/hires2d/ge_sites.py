"""GoldenEye 007 (USA) hi-res image: 2D emitter site table.

Image: the output of the recipe `goldeneye-007-usa-640x480`
(GE640x480iEnhanced[SubDragTrevorZoinkity].xdelta applied to the clean
DCBC50D1 dump). All 33 `sll rd, rt, 2 -> andi 0xFFF` coordinate packers
in that image are byte-identical to the clean ROM - the delta leaves
the whole 2D layer in 320-space, the same structural gap SM64 had.

Found with find2d.py; grouping by code region (function granularity is
what a hardware bisect can practically flash):

  boot/early   0x50xxx and 0x514xx    (4 sites)  - unknown role
  setsize      0xE1F04..0xE21C0       (3 sites)  - size-word packers
  blitter      0xE2BB4..0xE31BC       (19 sites) - the text/glyph
                texture-rectangle emitter (SETTILESIZE 0xE5, TEXRECT
                0xB4, TEXRECTFLIP 0xB3; step constants 0x0400/0xFC00)
  watch        0xFAD24..0xFAD58       (3 sites)  - near the HUD watch
  late         0x108068..0x108164     (5 sites)  - unknown role

STEP immediates (s5.10, sign-aware halving: 0x0400->0x0200,
0x0800->0x0400, 0xFC00->0xFE00) sit in the same blitter region.

NOTHING HERE IS VERIFIED ON HARDWARE. The bisect protocol that decides
is in this directory's README.
"""

GROUPS = {
    # everything find2d.py reports, for the "all in" variant
    "all": [
        0x0005015C,
        0x0005016C,
        0x00050574,
        0x0005140C,
        0x00051418,
        0x000E1F04,
        0x000E1F10,
        0x000E21C0,
        0x000E2BB4,
        0x000E2BBC,
        0x000E2E14,
        0x000E2FE8,
        0x000E2FFC,
        0x000E300C,
        0x000E3034,
        0x000E303C,
        0x000E3094,
        0x000E30AC,
        0x000E30B8,
        0x000E3104,
        0x000E3114,
        0x000E312C,
        0x000E3138,
        0x000E317C,
        0x000E319C,
        0x000E31AC,
        0x000E31B4,
        0x000FAD24,
        0x000FAD50,
        0x000FAD58,
        0x00108068,
        0x00108138,
        0x00108164,
    ],
    "boot": [
        0x0005015C,
        0x0005016C,
        0x00050574,
        0x0005140C,
        0x00051418,
    ],
    "setsize": [
        0x000E1F04,
        0x000E1F10,
        0x000E21C0,
    ],
    "blitter": [
        0x000E2BB4,
        0x000E2BBC,
        0x000E2E14,
        0x000E2FE8,
        0x000E2FFC,
        0x000E300C,
        0x000E3034,
        0x000E303C,
        0x000E3094,
        0x000E30AC,
        0x000E30B8,
        0x000E3104,
        0x000E3114,
        0x000E312C,
        0x000E3138,
        0x000E317C,
        0x000E319C,
        0x000E31AC,
        0x000E31B4,
    ],
    "watch": [
        0x000FAD24,
        0x000FAD50,
        0x000FAD58,
    ],
    "late": [
        0x00108068,
        0x00108138,
        0x00108164,
    ],
}

STEPS = {
    # ori immediates that build the s5.10 texture steps inside the
    # blitter region; halved only when --steps names them
    "blitter_steps": [
        0x000E2160,  # ori .., 0x0800
        0x000E2308,  # lui .., 0x0400  (half of the pair word)
        0x000E230C,  # ori .., 0xFC00
        0x000E23DC,  # ori .., 0x0400
        0x000E24B8,  # ori .., 0x0400
        0x000E25AC,  # ori .., 0x0400
        0x000E2DB8,  # ori .., 0x0800
        0x000E3074,  # ori .., 0xFC00
        0x000E30DC,  # ori .., 0x0400
        0x000E315C,  # ori .., 0x0400
        0x000E3200,  # ori .., 0x0400
    ],
}
