"""Build OoT (US 1.0) hi-res candidates from the clean dump.

The patch turns on the game's own runtime-width machinery (found via the
zeldaret/oot decomp build of this exact revision; addresses verified
against the built ELF):

- ViMode_Init: defaults viWidth 320->640, viHeight ->480 (480i) or ->240
  (240p), loRes 1->0, editState 0->ACTIVE(1). With editState active, the
  per-frame path copies viWidth/viHeight into gScreenWidth/gScreenHeight
  and selects customViMode, so scissors, color-image strides and most 2D
  follow automatically (z_rcp.c reads gScreenWidth).
- SysCfb_Init: framebuffer offsets/sizes; 480i relocates the framebuffer
  pair into the Expansion Pak area (0x80600000), 240p fits without it.
- View_Init: the 3D viewport (bottomY/rightX); the projection aspect is
  derived from the viewport, so the 3D scene fills the width.

The code segment is Yaz0-compressed in the ROM: it is decompressed with
crunch64, edited at virtual-address offsets, recompressed (must fit the
original slot) and spliced back. Every edit states the word it expects;
a mismatch aborts the build.

The candidate ROM is written to STDOUT (binary); all logs go to stderr.
Reads `clean.z64` and `flavor.txt` from the current directory
(flavor.txt: 480i, 240p, vitables or 640p; ROMs never enter the
repository).

Usage: python makeoot.py > cand_oot.z64
"""
import struct
import sys

import crunch64

FLAVORS = ("480i", "240p", "vitables", "640p")

CODE_VRAM = 0x800110A0
CODE_PSTART, CODE_PEND = 0xA62840, 0xAFD890
BOOT_TABLES = (0x6FC0, 0x7010)  # osViModeNtscLan1 / osViModeMpalLan1


def _err(*a):
    print(*a, file=sys.stderr)


def find_seq(data, words, start=0x1000):
    blob = b"".join(struct.pack(">I", w) for w in words)
    return data.find(blob, start)


def main():
    with open("flavor.txt") as f:
        flavor = f.read().strip()
    if flavor not in FLAVORS:
        raise SystemExit("flavor must be one of: " + ", ".join(FLAVORS))
    with open("clean.z64", "rb") as f:
        rom = bytearray(f.read())

    # --- decompressed code segment (the editing target) ------------------
    code = bytearray(crunch64.yaz0.decompress(bytes(rom[CODE_PSTART:CODE_PEND])))
    if len(code) != 0x103D30:
        raise SystemExit(f"unexpected code size {len(code):#x}")

    def cword(off):
        return struct.unpack_from(">I", code, off)[0]

    def cedit(off, new, expect, note):
        cur = cword(off)
        if cur != expect:
            raise SystemExit(
                f"MISMATCH at code+{off:05X}: expect {expect:08X}, found {cur:08X} ({note})")
        struct.pack_into(">I", code, off, new)
        _err(f"  code+{off:05X}: {expect:08X} -> {new:08X}  {note}")

    def rword(off):
        return struct.unpack_from(">I", rom, off)[0]

    def redit(off, new, expect, note):
        cur = rword(off)
        if cur != expect:
            raise SystemExit(
                f"MISMATCH at {off:08X}: expect {expect:08X}, found {cur:08X} ({note})")
        struct.pack_into(">I", rom, off, new)
        _err(f"  {off:08X}: {expect:08X} -> {new:08X}  {note}")

    # --- function anchors inside the decompressed code segment -----------
    # (from the ELF disassembly; each anchor is a unique instruction
    # sequence, verified word-for-word before patching)
    vi = find_seq(code, [0x24020001, 0x240E0140, 0x240F00F0, 0x24180042])  # ViMode_Init
    # SysCfb_Init fb-offset constant pairs (unique in the blob)
    cfb_f0 = find_seq(code, [0x3C01FFFB, 0x34215000])   # lui at,0xfffb / ori 0x5000
    cfb_f1 = find_seq(code, [0x3C01FFFD, 0x3421A800])   # lui at,0xfffd / ori 0xa800
    view = find_seq(code, [0x240E00F0, 0x240F0140])     # View_Init widths
    # Main(): boot re-assignment gScreenWidth/gScreenHeight = 320/240
    main_gsw = find_seq(code, [0x240E0140, 0x3C018010, 0xAC2EE500])
    # Scheduler's per-frame gScreenWidth store (lw t7,0x54(s0) first)
    sched_nop = find_seq(code, [0x8E0F0054, 0x3C018010, 0xAC2FE500]) + 0x8
    # gScreenWidth/gScreenHeight .data initializers
    gsw_data = 0x800FE500 - CODE_VRAM
    gsh_data = 0x800FE504 - CODE_VRAM
    for name, off in (("ViMode_Init", vi), ("SysCfb fb0", cfb_f0), ("SysCfb fb1", cfb_f1), ("View_Init", view)):
        if off < 0:
            raise SystemExit(f"anchor not found: {name}")
        _err(f"{name}: code+{off:05X}")

    # store offsets relative to the anchors, from the ELF disassembly:
    # ViMode_Init: +0x18 sw zero,0x68 (editState); +0x48 sw v0,0x78
    # (modeN); +0x4C sw v0,0x70 (loRes); +0x0C li t6,320; +0x10 li t7,240
    vi_state, vi_lores, vi_moden = vi + 0x18, vi + 0x4C, vi + 0x48
    vi_w, vi_h = vi + 0x0C, vi + 0x10
    # SysCfb_Init fb-offset pair words: hi at cfb_f0, lo at cfb_f0+4
    cfb_f0h, cfb_f0l = cfb_f0, cfb_f0 + 4
    cfb_f1h, cfb_f1l = cfb_f1, cfb_f1 + 4
    # View_Init: +0x00 li t6,240 (bottomY); +0x04 li t7,320 (rightX)
    view_h, view_w = view + 0x00, view + 0x04

    edits = []  # (kind, offset, new, expect, note)
    if flavor == "480i":
        edits += [
            ("c", vi_w, 0x240E0280, 0x240E0140, "viWidth 320->640"),
            ("c", vi_h, 0x240F01E0, 0x240F00F0, "viHeight 240->480"),
            ("c", vi_lores, 0xAC800070, 0xAC820070, "loRes 1->0"),
            ("c", vi_state, 0xAC820068, 0xAC800068, "editState 0->ACTIVE(1)"),
            # 8MB fb end moves to expansion: lui t6,0x8040 (at +0x40 in
            # SysCfb_Init = cfb_f0 - 0x50) -> lui t6,0x8060
            ("c", cfb_f0 - 0x50, 0x3C0F8060, 0x3C0F8040, "8MB fb end moves to expansion"),
            ("c", cfb_f0h, 0x3C01FFED, 0x3C01FFFB, "fb0 offset hi"),
            ("c", cfb_f0l, 0x34214000, 0x34215000, "fb0 offset lo"),
            ("c", cfb_f1h, 0x3C01FFF6, 0x3C01FFFD, "fb1 offset hi"),
            ("c", cfb_f1l, 0x3421A000, 0x3421A800, "fb1 offset lo"),
            ("c", view_h, 0x240E01E0, 0x240E00F0, "viewport bottomY 240->480"),
            ("c", view_w, 0x240F0280, 0x240F0140, "viewport rightX 320->640"),
        ]
    elif flavor == "240p":
        edits += [
            ("c", vi_w, 0x240E0280, 0x240E0140, "viWidth 320->640"),
            ("c", vi_lores, 0xAC800070, 0xAC820070, "loRes 1->0"),
            ("c", vi_moden, 0xAC800078, 0xAC820078, "modeN 1->0 (progressive)"),
            ("c", vi_state, 0xAC820068, 0xAC800068, "editState 0->ACTIVE(1)"),
            ("c", cfb_f0h, 0x3C01FFF6, 0x3C01FFFB, "fb0 offset hi"),
            ("c", cfb_f0l, 0x3421A000, 0x34215000, "fb0 offset lo"),
            ("c", cfb_f1h, 0x3C01FFFB, 0x3C01FFFD, "fb1 offset hi"),
            ("c", cfb_f1l, 0x34215000, 0x3421A800, "fb1 offset lo"),
            ("c", view_w, 0x240F0280, 0x240F0140, "viewport rightX 320->640"),
        ]
    elif flavor == "vitables":
        for base in (0x6FC0, 0x7010):
            edits += [
                ("r", base + 0x08, 0x00000280, 0x00000140, "table width 320->640"),
                ("r", base + 0x20, 0x00000400, 0x00000200, "table xScale 2.0->1.0"),
                ("r", base + 0x28, 0x00000500, 0x00000280, "table f0 origin 640->1280"),
                ("r", base + 0x3C, 0x00000500, 0x00000280, "table f1 origin 640->1280"),
            ]
    elif flavor == "640p":
        # 640x240 PROGRESSIVE without the ViMode editor hack (480i is
        # dead per hardware verdict - interlace unusable). Retail VI
        # path: the boot's static osViModeNtscLan1/MpalLan1 tables drive
        # the display, so the tables carry the 640-wide progressive
        # mode; the game renders 640-wide because gScreenWidth's .data
        # init AND Main()'s boot re-assignment are widened; SysCfb
        # framebuffers grow to 640x240 (same total bytes as stock -
        # fits a 4 MB console, no Expansion Pak needed); View viewport
        # widens so the 3D fills the framebuffer.
        for base in (0x6FC0, 0x7010):
            edits += [
                ("r", base + 0x08, 0x00000280, 0x00000140, "table width 320->640"),
                ("r", base + 0x20, 0x00000400, 0x00000200, "table xScale 2.0->1.0"),
                ("r", base + 0x28, 0x00000500, 0x00000280, "table f0 origin 640->1280"),
                ("r", base + 0x3C, 0x00000500, 0x00000280, "table f1 origin 640->1280"),
            ]
        edits += [
            # 8MB fb end 0x80400000 -> 0x80600000: the 640-wide framebuffer
            # pair is 0x4B000 bytes larger than stock and would otherwise
            # overlap the top of the game heap (boot hang)
            ("c", cfb_f0 - 0x50, 0x3C0F8060, 0x3C0F8040, "8MB fb end moves to expansion"),
            ("c", cfb_f0h, 0x3C01FFF6, 0x3C01FFFB, "fb0 offset hi"),
            ("c", cfb_f0l, 0x3421A000, 0x34215000, "fb0 offset lo"),
            ("c", cfb_f1h, 0x3C01FFFB, 0x3C01FFFD, "fb1 offset hi"),
            ("c", cfb_f1l, 0x34215000, 0x3421A800, "fb1 offset lo"),
            ("c", view_w, 0x240F0280, 0x240F0140, "viewport rightX 320->640"),
            ("c", gsw_data, 0x00000280, 0x00000140, "gScreenWidth .data 320->640"),
            ("c", main_gsw, 0x240E0280, 0x240E0140, "boot gScreenWidth 320->640"),
            ("c", sched_nop, 0x00000000, 0xAC2FE500, "scheduler overwrite -> NOP"),
        ]

    # --- apply -------------------------------------------------------------
    n = 0
    for kind, off, new, expect, note in edits:
        if kind == "r":
            redit(off, new, expect, note)
            n += 1
        else:
            cedit(off, new, expect, note)
            n += 1

    if flavor in ("480i", "240p", "640p"):
        comp = bytes(crunch64.yaz0.compress(bytes(code)))
        if len(comp) > CODE_PEND - CODE_PSTART:
            raise SystemExit(
                f"recompressed code {len(comp)} exceeds slot {CODE_PEND - CODE_PSTART}")
        rom[CODE_PSTART:CODE_PSTART + len(comp)] = comp
        _err(f"code recompressed: {len(comp)} bytes (slot {CODE_PEND - CODE_PSTART})")

    from n64patcher import n64_core as core

    crc = core.calculate_n64_crc(bytes(rom))
    if crc is None:
        raise SystemExit("CIC not identified - refusing to stamp")
    struct.pack_into(">II", rom, 0x10, crc[0], crc[1])

    sys.stdout.buffer.write(bytes(rom))
    _err(f"wrote candidate to stdout ({flavor}, {n} rom-segment edits)")


if __name__ == "__main__":
    main()
