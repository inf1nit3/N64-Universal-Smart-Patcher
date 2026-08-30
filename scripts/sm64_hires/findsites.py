"""Find every RDP texture-rectangle coordinate shift in the SM64 main segment.

The signature is precise rather than positional. A screen coordinate on its
way into a TEXRECT word is always `sll rd, rt, 2` (integer pixels -> the
RDP's 10.2 fixed point) whose result is then masked with `andi rX, rd,
0xFFF` (the field is 12 bits). Nothing else in this code does both. Matching
on that pair rather than on "a shift near an 0xE400" is what keeps the field
shifts (`sll rd, rt, 12`) and unrelated arithmetic out of the patch set.

Reports each site with the 0xE400 emitter it belongs to and the texture step
constants (dsdx, dtdy) that have to be halved alongside.
"""

import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_S, MAIN_E, RAM = 0x1000, 0x101000, 0x80246000
WINDOW = 8  # instructions to look ahead for the mask


def words(data):
    for off in range(MAIN_S, MAIN_E, 4):
        yield off, struct.unpack_from(">I", data, off)[0]


def is_sll(w, sa=None):
    if (w >> 26) or (w & 63) or w == 0:
        return False
    return sa is None or ((w >> 6) & 31) == sa


def main():
    with open(os.path.join(HERE, "hires.z64"), "rb") as f:
        data = f.read()
    wl = list(words(data))
    index = dict(wl)

    emitters = [off for off, w in wl if (w >> 26) == 0x0F and (w & 0xFFFF) == 0xE400]

    coord_shifts = []
    for off, w in wl:
        if not is_sll(w, 2):
            continue
        rd = (w >> 11) & 31
        for k in range(1, WINDOW + 1):
            nxt = index.get(off + 4 * k)
            if nxt is None:
                continue
            # andi rX, rd, 0x0FFF
            if (nxt >> 26) == 0x0C and ((nxt >> 21) & 31) == rd and (nxt & 0xFFFF) == 0x0FFF:
                coord_shifts.append(off)
                break

    print(f"{len(emitters)} TEXRECT emitters, {len(coord_shifts)} coordinate shifts\n")

    for e in emitters:
        near = [o for o in coord_shifts if abs(o - e) <= 0x100]
        scale = []
        for k in range(-0x40, 0x120, 4):
            w = index.get(e + k)
            if w is None:
                continue
            op, imm = w >> 26, w & 0xFFFF
            if op in (0x0F, 0x0D) and imm in (0x1000, 0x0400):
                scale.append((e + k, "lui" if op == 0x0F else "ori", imm))
        print(f"emitter rom {e:08X}  ram {RAM + e - MAIN_S:08X}")
        print(f"  coords: {' '.join(f'{o:06X}' for o in sorted(near))}   ({len(near)})")
        print(f"  scale : {' '.join(f'{o:06X}={k}:{v:04X}' for o, k, v in scale)}")

    orphan = [o for o in coord_shifts if not any(abs(o - e) <= 0x100 for e in emitters)]
    if orphan:
        print(
            f"\ncoordinate shifts with no emitter within 0x100: "
            f"{' '.join(f'{o:06X}' for o in orphan)}"
        )


if __name__ == "__main__":
    main()
