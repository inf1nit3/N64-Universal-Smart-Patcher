"""Minimal MIPS III disassembler, enough to read SM64's display-list builders.

Covers the opcodes that actually appear in this code: the I-type arithmetic
and load/store forms, the SPECIAL shifts and three-operand ALU ops, the
branches, and the jumps. Anything unknown prints as a raw word rather than
a guess, so a misread is visible instead of silent.
"""

import struct

REG = [
    "zero",
    "at",
    "v0",
    "v1",
    "a0",
    "a1",
    "a2",
    "a3",
    "t0",
    "t1",
    "t2",
    "t3",
    "t4",
    "t5",
    "t6",
    "t7",
    "s0",
    "s1",
    "s2",
    "s3",
    "s4",
    "s5",
    "s6",
    "s7",
    "t8",
    "t9",
    "k0",
    "k1",
    "gp",
    "sp",
    "fp",
    "ra",
]

SPECIAL = {
    0x00: "sll",
    0x02: "srl",
    0x03: "sra",
    0x04: "sllv",
    0x06: "srlv",
    0x07: "srav",
    0x08: "jr",
    0x09: "jalr",
    0x0C: "syscall",
    0x0D: "break",
    0x10: "mfhi",
    0x11: "mthi",
    0x12: "mflo",
    0x13: "mtlo",
    0x18: "mult",
    0x19: "multu",
    0x1A: "div",
    0x1B: "divu",
    0x20: "add",
    0x21: "addu",
    0x22: "sub",
    0x23: "subu",
    0x24: "and",
    0x25: "or",
    0x26: "xor",
    0x27: "nor",
    0x2A: "slt",
    0x2B: "sltu",
}

ITYPE = {
    0x08: "addi",
    0x09: "addiu",
    0x0A: "slti",
    0x0B: "sltiu",
    0x0C: "andi",
    0x0D: "ori",
    0x0E: "xori",
    0x20: "lb",
    0x21: "lh",
    0x22: "lwl",
    0x23: "lw",
    0x24: "lbu",
    0x25: "lhu",
    0x26: "lwr",
    0x28: "sb",
    0x29: "sh",
    0x2A: "swl",
    0x2B: "sw",
    0x2E: "swr",
    0x31: "lwc1",
    0x39: "swc1",
    0x35: "ldc1",
    0x3D: "sdc1",
    0x37: "ld",
    0x3F: "sd",
}

BRANCH = {
    0x04: "beq",
    0x05: "bne",
    0x06: "blez",
    0x07: "bgtz",
    0x14: "beql",
    0x15: "bnel",
    0x16: "blezl",
    0x17: "bgtzl",
}


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def disasm(word, pc):
    op = word >> 26
    rs, rt, rd = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    sa, funct, imm = (word >> 6) & 31, word & 63, word & 0xFFFF

    if op == 0:
        name = SPECIAL.get(funct)
        if name is None:
            return f".word 0x{word:08X}"
        if word == 0:
            return "nop"
        if name in ("sll", "srl", "sra"):
            return f"{name} {REG[rd]}, {REG[rt]}, {sa}"
        if name in ("sllv", "srlv", "srav"):
            return f"{name} {REG[rd]}, {REG[rt]}, {REG[rs]}"
        if name == "jr":
            return f"jr {REG[rs]}"
        if name == "jalr":
            return f"jalr {REG[rd]}, {REG[rs]}"
        if name in ("mfhi", "mflo"):
            return f"{name} {REG[rd]}"
        if name in ("mult", "multu", "div", "divu"):
            return f"{name} {REG[rs]}, {REG[rt]}"
        return f"{name} {REG[rd]}, {REG[rs]}, {REG[rt]}"

    if op == 0x0F:
        return f"lui {REG[rt]}, 0x{imm:04X}"
    if op in BRANCH:
        target = pc + 4 + (s16(imm) << 2)
        if op in (0x06, 0x07, 0x16, 0x17):
            return f"{BRANCH[op]} {REG[rs]}, 0x{target:08X}"
        return f"{BRANCH[op]} {REG[rs]}, {REG[rt]}, 0x{target:08X}"
    if op == 0x01:
        name = {0: "bltz", 1: "bgez", 16: "bltzal", 17: "bgezal"}.get(rt)
        if name:
            return f"{name} {REG[rs]}, 0x{pc + 4 + (s16(imm) << 2):08X}"
    if op in (0x02, 0x03):
        target = (pc & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
        return f"{'j' if op == 2 else 'jal'} 0x{target:08X}"
    if op in ITYPE:
        name = ITYPE[op]
        if name in ("addi", "addiu", "slti", "sltiu"):
            return f"{name} {REG[rt]}, {REG[rs]}, {s16(imm)}"
        if name in ("andi", "ori", "xori"):
            return f"{name} {REG[rt]}, {REG[rs]}, 0x{imm:04X}"
        return f"{name} {REG[rt]}, {s16(imm)}({REG[rs]})"
    return f".word 0x{word:08X}"


def dump(data, rom_off, count, ram_base, rom_base, mark=()):
    """Print `count` instructions starting at rom_off."""
    out = []
    for i in range(count):
        o = rom_off + i * 4
        w = struct.unpack(">I", data[o : o + 4])[0]
        pc = ram_base + (o - rom_base)
        flag = "*" if o in mark else " "
        out.append(f"{flag} {o:08X} {pc:08X}  {w:08X}  {disasm(w, pc)}")
    return "\n".join(out)
