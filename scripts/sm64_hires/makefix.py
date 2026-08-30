"""Build the SM64 hi-res 2D fix on top of the SubDrag 640x480 image.

The idea, in one paragraph. The SubDrag delta moves the framebuffer,
viewport, scissor and every geo-layout screen area to 640x480, but it does
not touch the 2D layer, because RDP rectangle commands carry raw screen
coordinates and bypass the transform pipeline entirely - no matrix can
scale them. What can be scaled is the shift that converts those coordinates
into the RDP's 10.2 fixed-point form. The code already does `x << 2`; making
it `x << 3` doubles the coordinate and leaves it in 10.2, at zero
instruction cost. Pair that with halving the texture step (dsdx, dtdy) so
the glyph stretches over twice the pixels instead of repeating, and one
texture-rectangle emitter is corrected by six word edits.

Sites come from findsites.py, which matches on `sll rd, rt, 2` feeding an
`andi rX, rd, 0xFFF` - the only thing in this code that does both, so field
shifts and unrelated arithmetic cannot be swept in by accident.

Every edit states the word it expects to find. A mismatch aborts the whole
build rather than writing into whatever happens to be at that offset.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Emitters, in the order findsites.py reports them, with a note on what each
# one draws. The names are inferred from behaviour, not from symbols; the
# ROM carries none.
SITES = [
    (
        0x00091BB8,
        "print.c render_textrect - HUD font: coins, stars, lives, timer",
        [0x091BC0, 0x091BD0, 0x091BF4, 0x091BFC],
        0x091C64,
        0x091C68,
    ),
    (
        0x00092D80,
        "ingame_menu.c - generic 16x16 glyph (menu text)",
        [0x092D88, 0x092D98, 0x092DBC, 0x092DC4],
        0x092E2C,
        0x092E30,
    ),
    (
        0x00093020,
        "ingame_menu.c - generic glyph, second form",
        [0x093028, 0x093038, 0x09305C, 0x093064],
        0x0930CC,
        0x0930D0,
    ),
    (
        0x000931A4,
        "ingame_menu.c - generic glyph, third form",
        [0x0931AC, 0x0931BC, 0x0931E0, 0x0931EC],
        0x093258,
        0x09325C,
    ),
    (
        0x00093520,
        "ingame_menu.c - generic glyph, fourth form",
        [0x093528, 0x093538, 0x09355C, 0x093564],
        0x0935CC,
        0x0935D0,
    ),
    (
        0x0009DDB4,
        "HUD LUT char - large digits and icons",
        [0x09DD9C, 0x09DDAC, 0x09DDC8, 0x09DDD4],
        0x09DE3C,
        0x09DE40,
    ),
    (
        0x0009E010,
        "HUD LUT char, second form",
        [0x09DFF8, 0x09E008, 0x09E024, 0x09E030],
        0x09E098,
        0x09E09C,
    ),
]

# Which emitters to include. Trimming this is how a site gets ruled out after
# a hardware test without touching anything else. Override from the command
# line: `makefix.py out.z64 0,1,2,3,4` builds with sites 5 and 6 left alone.
ENABLED = set(range(len(SITES)))


def shift_to_3(word):
    """`sll rd, rt, 2` -> `sll rd, rt, 3`. The shift amount is bits 6-10."""
    assert (word >> 26) == 0 and (word & 63) == 0, f"not a shift: {word:08X}"
    assert ((word >> 6) & 31) == 2, f"shift is not 2: {word:08X}"
    return word + (1 << 6)


def halve_imm(word):
    """Halve the 16-bit immediate of a lui/ori."""
    op = word >> 26
    assert op in (0x0F, 0x0D), f"not lui/ori: {word:08X}"
    imm = word & 0xFFFF
    assert imm and imm % 2 == 0, f"immediate not halvable: {imm:04X}"
    return (word & 0xFFFF0000) | (imm >> 1)


def crc_fix(data):
    """Recompute CRC1/CRC2 with the project's own engine.

    Not cosmetic: the boot checksums are verified by the CIC at power-on,
    so a ROM with stale values black-screens on real hardware.
    """
    from n64patcher import n64_core

    result = n64_core.calculate_n64_crc(data)
    if result is None:
        raise SystemExit("CIC chip not identified - refusing to stamp a CRC")
    crc1, crc2 = result
    out = bytearray(data)
    struct.pack_into(">II", out, 0x10, crc1, crc2)
    return bytes(out), crc1, crc2


def main():
    src = os.path.join(HERE, "hires.z64")
    dst = os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "hires_fixed.z64")
    enabled = {int(x) for x in sys.argv[2].split(",") if x != ""} if len(sys.argv) > 2 else ENABLED
    with open(src, "rb") as f:
        data = bytearray(f.read())
    edits = 0

    for i, (emitter, note, coords, dsdx, dtdy) in enumerate(SITES):
        state = "on " if i in enabled else "OFF"
        print(f"[{state}] site {i} rom {emitter:08X}  {note}")
        if i not in enabled:
            continue
        for off in coords:
            w = struct.unpack_from(">I", data, off)[0]
            new = shift_to_3(w)
            struct.pack_into(">I", data, off, new)
            print(f"        {off:08X}  {w:08X} -> {new:08X}  coord << 2 -> << 3")
            edits += 1
        for off in (dsdx, dtdy):
            w = struct.unpack_from(">I", data, off)[0]
            new = halve_imm(w)
            struct.pack_into(">I", data, off, new)
            print(f"        {off:08X}  {w:08X} -> {new:08X}  texture step halved")
            edits += 1

    data, crc1, crc2 = crc_fix(bytes(data))
    with open(dst, "wb") as f:
        f.write(data)
    print(f"\n{edits} words patched across {len(enabled)} sites")
    print(f"CRC1 {crc1:08X}  CRC2 {crc2:08X}")
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
