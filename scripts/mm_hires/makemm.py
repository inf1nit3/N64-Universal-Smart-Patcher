"""Build Majora's Mask (US) hi-res candidates from the clean dump.

MM ships its own hi-res machinery: SysCfb_SetHiResMode() switches to
statically-allocated 576x454 framebuffers/z-buffer (Expansion Pak area,
0x807EA800) and a progressive "notebook" VI mode built by
ViMode_Configure (576 wide, leftAdjust 30, upperAdjust 10). Retail only
enters it while the Bombers Notebook screen is open. This patch makes
that mode permanent:

- Play_Draw's `if (sBombersNotebookOpen)` branch is taken unconditionally
  (beq->bne), so the first Play_Draw switches to hi-res and stays there.
- View_Init's viewport constants widen from 320x240 to 576x454 so the 3D
  fills the framebuffer (same fix as OoT's View_Init). Before the switch
  the scissor (gScreenWidth=320) still bounds rendering, so the boot
  states are unaffected.

Addresses (all verified against the decompressed code segment, code
vram base 0x800A5AC0):

- code+0x0C331C: beq v1,zero (sBombersNotebookOpen) -> bne
- code+0x09946C: li t6,240 -> li t6,454 (View_Init viewport bottomY)
- code+0x099470: li t7,320 -> li t7,576 (View_Init viewport rightX)

The code segment lives at vrom 0xB3C000 (yaz0, rom 0xA684D0..0xB26590,
decompresses to 0x13E4E0 bytes). The recompressed stream fits the ROM
slot with ~2 bytes to spare - keep edits minimal. The CIC checksum
window does not cover the code segment, so the ROM CRCs are unchanged.

Verified in mupen64plus: the intro cutscene renders full-screen
576x454 progressive. The title/file-select states afterwards render
with 320-space UI into the hi-res framebuffer (garbled) - they need
the same per-renderer 2D pass as OoT; auto-advance input patches for
unattended runs are not built yet.

Usage: python makemm.py > cand_mm_hires.z64
Reads the clean dump from ../work/mm/baseroms/n64-us/baserom.z64.
All logs go to stderr, the candidate goes to stdout.
"""
import struct
import sys

import crunch64

CODE_ROM_START, CODE_ROM_END = 0xA684D0, 0xB26590

EDITS = [
    (0x0C331C, 0x10600025, 0x14600025, "Play_Draw: notebook-branch always taken"),
    (0x09946C, 0x240E00F0, 0x240E01C6, "View_Init: viewport bottomY 240->454"),
    (0x099470, 0x240F0140, 0x240F0240, "View_Init: viewport rightX 320->576"),
]


def _err(*a):
    print(*a, file=sys.stderr)


def main():
    import os

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "mm",
                        "baseroms", "n64-us", "baserom.z64")
    with open(base, "rb") as f:
        rom = bytearray(f.read())

    code = bytearray(crunch64.yaz0.decompress(bytes(rom[CODE_ROM_START:CODE_ROM_END])))
    for off, expect, new, note in EDITS:
        cur = struct.unpack_from(">I", code, off)[0]
        if cur != expect:
            raise SystemExit(f"MISMATCH at code+{off:06X}: expect {expect:08X}, found {cur:08X} ({note})")
        struct.pack_into(">I", code, off, new)
        _err(f"  code+0x{off:06X}: {expect:08X} -> {new:08X}  {note}")

    comp = bytes(crunch64.yaz0.compress(bytes(code)))
    slot = CODE_ROM_END - CODE_ROM_START
    if len(comp) > slot:
        raise SystemExit(f"recompressed code {len(comp)} exceeds slot {slot}")
    rom[CODE_ROM_START:CODE_ROM_START + len(comp)] = comp
    _err(f"code recompressed: {len(comp)} bytes (slot {slot})")

    sys.stdout.buffer.write(bytes(rom))
    _err("wrote candidate to stdout (ROM CRCs unchanged: code segment is outside the CIC window)")


if __name__ == "__main__":
    main()
