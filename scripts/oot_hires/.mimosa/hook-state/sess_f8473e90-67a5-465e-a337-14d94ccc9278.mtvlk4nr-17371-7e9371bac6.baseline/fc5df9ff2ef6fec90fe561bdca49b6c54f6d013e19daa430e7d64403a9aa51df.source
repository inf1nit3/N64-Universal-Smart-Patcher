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

Reads `clean.z64` next to this script and always writes `cand_oot.z64`
next to it (rename after each run; ROMs never enter the repository).
The flavor comes from `flavor.txt`: 480i, 240p or vitables.

Usage: python makeoot.py
"""

import os
import struct

import crunch64

FLAVORS = ("480i", "240p", "vitables")

# All file I/O is confined to this script's own directory: every name is
# a fixed literal, resolved and verified against the script directory
# before any open().
_HERE = os.path.dirname(os.path.abspath(__file__))


def _local(name):
    """Resolve a fixed filename inside this script's directory."""
    path = os.path.normpath(os.path.join(_HERE, name))
    if not path.startswith(_HERE + os.sep):
        raise SystemExit(f"refusing to touch {path!r} outside the script directory")
    return path


CODE_VRAM = 0x800110A0
CODE_PSTART, CODE_PEND = 0xA62840, 0xAFD890


def find_seq(data, words, start=0x1000):
    blob = b"".join(struct.pack(">I", w) for w in words)
    return data.find(blob, start)


def main():
    with open(_local("flavor.txt")) as f:
        flavor = f.read().strip()
    if flavor not in FLAVORS:
        raise SystemExit("flavor must be one of: " + ", ".join(FLAVORS))
    with open(_local("clean.z64"), "rb") as f:
        rom = bytearray(f.read())

    code = bytearray(crunch64.yaz0.decompress(bytes(rom[CODE_PSTART:CODE_PEND])))
    if len(code) != 0x103D30:
        raise SystemExit(f"unexpected code size {len(code):#x}")

    def code_off(vram):
        return vram - CODE_VRAM

    def word(buf, off):
        return struct.unpack_from(">I", buf, off)[0]

    def edit(buf, off, new, expect, note):
        cur = word(buf, off)
        if cur != expect:
            raise SystemExit(
                f"MISMATCH at {off:08X}: expect {expect:08X}, found {cur:08X} ({note})"
            )
        struct.pack_into(">I", buf, off, new)
        print(f"  {off:08X}: {expect:08X} -> {new:08X}  {note}")

    # function starts in the decompressed code segment (VRAM from the map)
    vi = code_off(0x80093550)  # ViMode_Init
    cfb = code_off(0x800A42F0)  # SysCfb_Init
    view = code_off(0x80091858)  # View_Init

    # verify anchors against the ELF disassembly before editing
    expect = {
        vi + 0x00: 0x27BDFFE8,
        vi + 0x04: 0xAFBF0014,
        vi + 0x08: 0x24020001,
        vi + 0x0C: 0x240E0140,
        vi + 0x10: 0x240F00F0,
        vi + 0x18: 0xAC800068,
        vi + 0x44: 0xAC820078,
        vi + 0x4C: 0xAC820070,
        cfb + 0x00: 0x27BDFFE8,
        cfb + 0x08: 0x3C028000,
        cfb + 0x40: 0x3C0F8040,
        cfb + 0x90: 0x3C01FFFB,
        cfb + 0x94: 0x34215000,
        cfb + 0xA8: 0x3C01FFFD,
        cfb + 0xAC: 0x3421A800,
        view + 0x40: 0x240E00F0,
        view + 0x44: 0x240F0140,
    }
    for off, exp in expect.items():
        cur = word(code, off)
        if cur != exp:
            raise SystemExit(f"anchor verify failed at +{off:05X}: {cur:08X} != {exp:08X}")
    print("anchors verified against ELF disassembly")

    # store offsets relative to the anchors, from the ELF disassembly:
    # ViMode_Init: +0x18 sw zero,0x68 (editState); +0x48 sw v0,0x78
    # (modeN); +0x4C sw v0,0x70 (loRes); +0x08 li t6,320; +0x0C li t7,240
    vi_state, vi_lores, vi_moden = vi + 0x18, vi + 0x4C, vi + 0x44
    vi_w, vi_h = vi + 0x0C, vi + 0x10
    # SysCfb_Init: +0x40 lui t6 (8MB fb end); +0x90/+0x94 fb0 offset;
    # +0xA8/+0xAC fb1 offset
    cfb_end, cfb_f0h, cfb_f0l = cfb + 0x40, cfb + 0x90, cfb + 0x94
    cfb_f1h, cfb_f1l = cfb + 0xA8, cfb + 0xAC
    # View_Init: +0x40 li t6,240 (bottomY); +0x44 li t7,320 (rightX)
    view_h, view_w = view + 0x40, view + 0x44

    edits = []
    if flavor == "480i":
        edits += [
            (vi_w, 0x240E0280, "viWidth 320->640"),
            (vi_h, 0x240F01E0, "viHeight 240->480"),
            (vi_lores, 0xAC800070, "loRes 1->0"),
            (vi_state, 0xAC820068, "editState 0->ACTIVE(1)"),
            (cfb_end, 0x3C0F8060, "8MB fb end moves to expansion"),
            (cfb_f0h, 0x3C01FFED, "fb0 offset hi"),
            (cfb_f0l, 0x34214000, "fb0 offset lo"),
            (cfb_f1h, 0x3C01FFF6, "fb1 offset hi"),
            (cfb_f1l, 0x3421A000, "fb1 offset lo"),
            (view_h, 0x240E01E0, "viewport bottomY 240->480"),
            (view_w, 0x240F0280, "viewport rightX 320->640"),
        ]
    elif flavor == "240p":
        edits += [
            (vi_w, 0x240E0280, "viWidth 320->640"),
            (vi_lores, 0xAC800070, "loRes 1->0"),
            (vi_moden, 0xAC800078, "modeN 1->0 (progressive)"),
            (vi_state, 0xAC820068, "editState 0->ACTIVE(1)"),
            (cfb_f0h, 0x3C01FFF6, "fb0 offset hi"),
            (cfb_f0l, 0x3421A000, "fb0 offset lo"),
            (cfb_f1h, 0x3C01FFFB, "fb1 offset hi"),
            (cfb_f1l, 0x34215000, "fb1 offset lo"),
            (view_w, 0x240F0280, "viewport rightX 320->640"),
        ]
    elif flavor == "vitables":
        for base in (0x6FC0, 0x7010):
            edits += [
                (base + 0x08, 0x00000280, "table width 320->640"),
                (base + 0x20, 0x00000400, "table xScale 2.0->1.0"),
                (base + 0x28, 0x00000500, "table f0 origin 1280"),
                (base + 0x3C, 0x00000500, "table f1 origin 1280"),
            ]

    for off, new, note in edits:
        if off < 0x8000:  # the static VI tables live in the boot segment
            edit(rom, off, new, word(rom, off), note)
        else:
            edit(code, off, new, word(code, off), note)

    if flavor in ("480i", "240p"):
        comp = bytes(crunch64.yaz0.compress(bytes(code)))
        if len(comp) > CODE_PEND - CODE_PSTART:
            raise SystemExit(
                f"recompressed code {len(comp)} exceeds slot {CODE_PEND - CODE_PSTART}"
            )
        rom[CODE_PSTART : CODE_PSTART + len(comp)] = comp
        print(f"code recompressed: {len(comp)} bytes (slot {CODE_PEND - CODE_PSTART})")

    from n64patcher import n64_core as core

    crc = core.calculate_n64_crc(bytes(rom))
    if crc is None:
        raise SystemExit("CIC not identified - refusing to stamp")
    struct.pack_into(">II", rom, 0x10, crc[0], crc[1])

    with open("cand_oot.z64", "wb") as f:
        f.write(bytes(rom))
    print(f"wrote cand_oot.z64 ({flavor}, {len(edits)} edits)")


if __name__ == "__main__":
    main()
