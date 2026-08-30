"""Locate the 2D layer (menu/HUD) emitters in a hi-res N64 image.

Generalises scripts/sm64_hires/findsites.py from one engine to a survey:
it does not assume the SM64 idiom, it looks for the two ways a 2D layer
is built and reports both.

1. Code that packs a pixel coordinate into the RDP's s10.2 fixed-point
   form on the fly - `sll rd, rt, 2` feeding `andi rX, rd, 0xFFF` (the
   SM64 signature). Shift by 2 == 320-space; a fix is shift by 3.

2. Static texture-rectangle commands sitting in ROM display lists
   (menus are usually prebuilt). The command word of G_TEXRECT/
   G_TEXRECT_WIDE is 0xB4/0xB5 in the high byte, with screen coords in
   10.2 already packed. Reporting their distribution tells us whether
   the delta moved the static lists to 640-space or left them at 320.

Usage:
    python find2d.py hires.z64            # both surveys
    python find2d.py hires.z64 --dl-only  # static display lists only
"""

import argparse
import struct

TEXRECT_OPS = (0xB4, 0xB5)


def word_runs(data):
    for off in range(0, len(data) - 3, 4):
        yield off, struct.unpack_from(">I", data, off)[0]


def find_shift_packers(data):
    """SM64-style: sll rd, rt, 2 whose result feeds andi rX, rd, 0xFFF."""
    words = {}
    hits = []
    for off, w in word_runs(data):
        words[off] = w
    for off, w in words.items():
        if (w >> 26) or (w & 0x3F) or ((w >> 6) & 31) != 2:
            continue
        rd = (w >> 11) & 0x1F
        # an andi rd, rs, 0xFFF within the next few instructions that
        # reads the sll's destination
        for step in (4, 8, 12):
            user = words.get(off + step)
            if user is None:
                continue
            if (user >> 26) == 0x0C:  # andi
                rs = (user >> 21) & 0x1F
                if rs == rd and (user & 0xFFFF) == 0x0FFF:
                    hits.append((off, w, user))
                    break
    return hits


def find_static_texrects(data):
    """Texture-rectangle commands in ROM, classified by coordinate space.

    A G_TEXRECT word is followed by two words of s10.2 coordinates
    (xl, yl, xh, yh packed 12/12/12/12 - the low 2 bits are the
    fractional part). Screen-space extents of 320x240 or 640x480 in the
    high halves are what the classification keys on.
    """
    rects = []
    for off, w in word_runs(data):
        if (w >> 24) not in TEXRECT_OPS:
            continue
        if off + 12 > len(data):
            continue
        coords = struct.unpack_from(">II", data, off + 4)
        rects.append((off, w, coords))
    return rects


def classify(rects):
    """Bucket static rectangles by their apparent coordinate space."""
    spaces = {"320": 0, "640": 0, "other": 0}
    for _off, _w, (c1, _c2) in rects:
        xl, yl = (c1 >> 14) & 0x3FF, (c1 >> 2) & 0x3FF
        if xl <= 330 and yl <= 250:
            spaces["320"] += 1
        elif xl <= 650 and yl <= 490:
            spaces["640"] += 1
        else:
            spaces["other"] += 1
    return spaces


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="the hi-res (patched) .z64 image")
    ap.add_argument("--dl-only", action="store_true")
    args = ap.parse_args()

    with open(args.image, "rb") as f:
        data = f.read()

    if not args.dl_only:
        packers = find_shift_packers(data)
        print(f"code packers (sll <<2 -> andi 0xFFF): {len(packers)}")
        for off, w, user in packers[:40]:
            print(f"  rom {off:08X}  sll {w:08X}  andi {user:08X}")
        if len(packers) > 40:
            print(f"  … and {len(packers) - 40} more")

    rects = find_static_texrects(data)
    spaces = classify(rects)
    print(f"\nstatic texture-rectangle commands: {len(rects)}")
    print(f"  coordinate space: {spaces}")
    by_mb = {}
    for off, _w, _c in rects:
        by_mb[off >> 20] = by_mb.get(off >> 20, 0) + 1
    for mb in sorted(by_mb):
        print(f"  0x{mb << 20:08X}: {by_mb[mb]}")
    # A sample of 320-space rects: these are the ones a fix must reach.
    shown = 0
    for off, w, (c1, _c2) in rects:
        xl, yl = (c1 >> 14) & 0x3FF, (c1 >> 2) & 0x3FF
        if xl <= 330 and yl <= 250:
            print(f"  320-space rom {off:08X}  cmd {w:08X}  xy ({xl},{yl})")
            shown += 1
            if shown >= 25:
                print("  …")
                break


if __name__ == "__main__":
    main()
