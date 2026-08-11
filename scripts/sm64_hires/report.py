"""Classify the clean -> hi-res delta for SM64 (U).

Reads clean.z64 and hires.z64 from this directory and prints every changed
region in the main code segment interpreted three ways: as a 16-bit value,
as the immediate field of the enclosing MIPS instruction, and as raw bytes.
The point is to see which constants SubDrag doubled and which he did not.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_ROM_START = 0x1000
MAIN_ROM_END = 0x101000
MAIN_RAM_BASE = 0x80246000


def ram_of(rom):
    return MAIN_RAM_BASE + (rom - MAIN_ROM_START)


def runs_of(a, b):
    out = []
    i, n = 0, len(a)
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
    return w, struct.unpack(">I", buf[w:w + 4])[0]


def imm(word):
    """Signed 16-bit immediate field of a MIPS I-type instruction."""
    v = word & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


OPC = {
    0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu",
    0x0C: "andi", 0x0D: "ori", 0x0E: "xori", 0x0F: "lui",
    0x20: "lb", 0x21: "lh", 0x23: "lw", 0x24: "lbu", 0x25: "lhu",
    0x28: "sb", 0x29: "sh", 0x2B: "sw",
    0x31: "lwc1", 0x39: "swc1", 0x35: "ldc1", 0x3D: "sdc1",
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
    with open(os.path.join(HERE, "clean.z64"), "rb") as f:
        a = f.read()
    with open(os.path.join(HERE, "hires.z64"), "rb") as f:
        b = f.read()
    runs = runs_of(a, b)
    scope = sys.argv[1] if len(sys.argv) > 1 else "main"

    shown = 0
    for off, length in runs:
        in_main = MAIN_ROM_START <= off < MAIN_ROM_END
        if scope == "main" and not in_main:
            continue
        if scope == "other" and in_main:
            continue
        shown += 1
        _, wa = word_at(a, off)
        _, wb = word_at(b, off)
        line = [f"rom {off:08X}"]
        if in_main:
            line.append(f"ram {ram_of(off):08X}")
        line.append(f"len {length}")
        line.append(f"word {wa:08X} -> {wb:08X}")

        ia, ib = imm(wa), imm(wb)
        if ia and ib:
            ratio = ib / ia
            line.append(f"imm {ia} -> {ib}" +
                        (f" (x{ratio:.3g})" if abs(ratio - round(ratio, 3)) < 1e-9 else ""))
        d = describe(wa)
        if d:
            line.append(d)
        print("  ".join(line))
    print(f"\n{shown} runs in scope '{scope}' (of {len(runs)} total)")


if __name__ == "__main__":
    main()
