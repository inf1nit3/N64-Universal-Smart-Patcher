"""Classify a clean -> hi-res delta for any game.

Generalises scripts/sm64_hires/report.py: takes explicit paths, keeps the
three-way interpretation of every changed region (as a value, as the
immediate of the enclosing MIPS instruction, as raw bytes), and adds a
histogram of where the changes land. The point is always the same: see
which constants the delta doubled and which 2D-layer constants it left
alone - the untouched ones are what a Stage 1b game fix has to cover.

Usage:
    python diffreport.py clean.z64 hires.z64 [main|other|all] [--ram BASE]
"""

import argparse
import struct

MAIN_ROM_START = 0x1000
MAIN_ROM_END = 0x101000


def ram_of(rom, base):
    return base + (rom - MAIN_ROM_START)


def runs_of(a, b):
    out = []
    i, n = 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def word_at(buf, off):
    w = off & ~3
    if w + 4 > len(buf):
        return w, None
    return w, struct.unpack(">I", buf[w : w + 4])[0]


def imm(word):
    v = word & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


OPC = {
    0x08: "addi",
    0x09: "addiu",
    0x0A: "slti",
    0x0B: "sltiu",
    0x0C: "andi",
    0x0D: "ori",
    0x0E: "xori",
    0x0F: "lui",
    0x20: "lb",
    0x21: "lh",
    0x23: "lw",
    0x24: "lbu",
    0x25: "lhu",
    0x28: "sb",
    0x29: "sh",
    0x2B: "sw",
    0x31: "lwc1",
    0x39: "swc1",
    0x35: "ldc1",
    0x3D: "sdc1",
}


def describe(word):
    op = (word >> 26) & 0x3F
    name = OPC.get(op)
    if name is None:
        return None
    rs = (word >> 21) & 0x1F
    rt = (word >> 16) & 0x1F
    return f"{name} r{rt}, {imm(word)}(r{rs})"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("clean")
    ap.add_argument("hires")
    ap.add_argument("scope", nargs="?", default="all", choices=["main", "other", "all"])
    ap.add_argument(
        "--ram",
        type=lambda x: int(x, 0),
        default=0,
        help="RAM base of the main segment, e.g. 0x80246000",
    )
    args = ap.parse_args()

    with open(args.clean, "rb") as f:
        a = f.read()
    with open(args.hires, "rb") as f:
        b = f.read()
    if len(a) != len(b):
        print(f"note: sizes differ ({len(a)} -> {len(b)}); diffing the common prefix only")
    runs = runs_of(a, b)

    shown = 0
    for off, length in runs:
        in_main = MAIN_ROM_START <= off < MAIN_ROM_END
        if args.scope == "main" and not in_main:
            continue
        if args.scope == "other" and in_main:
            continue
        shown += 1
        _, wa = word_at(a, off)
        _, wb = word_at(b, off)
        line = [f"rom {off:08X}"]
        if in_main and args.ram:
            line.append(f"ram {ram_of(off, args.ram):08X}")
        line.append(f"len {length}")
        if wa is not None and wb is not None:
            line.append(f"word {wa:08X} -> {wb:08X}")
            ia, ib = imm(wa), imm(wb)
            if ia and ib:
                ratio = ib / ia
                exact = abs(ratio - round(ratio, 3)) < 1e-9
                line.append(f"imm {ia} -> {ib}" + (f" (x{ratio:.3g})" if exact else ""))
            d = describe(wa)
            if d:
                line.append(d)
        print("  ".join(line))

    inside = sum(1 for off, _ in runs if MAIN_ROM_START <= off < MAIN_ROM_END)
    print(
        f"\n{shown} runs in scope '{args.scope}' "
        f"(of {len(runs)} total; {inside} in the main code segment, "
        f"{len(runs) - inside} elsewhere)"
    )


if __name__ == "__main__":
    main()
