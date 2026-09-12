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

With --dbg the candidate additionally patches the file-select overlay
(vrom 0xC7E4F0, yaz0, rom 0xB28DA0..0xB326E0) for unattended runs:
the main menu's START|A check is forced (andi->ori), auto-selecting
File 1 into the name entry flow.

Usage: python makemm.py [--dbg] > candidate.z64
Reads the clean dump from ../work/mm/baseroms/n64-us/baserom.z64.
All logs go to stderr, the candidate goes to stdout.
"""
import struct
import sys

import crunch64

CODE_ROM_START, CODE_ROM_END = 0xA684D0, 0xB26590
FS_ROM_START, FS_ROM_END = 0xB28DA0, 0xB326E0

EDITS = [
    (0x0C331C, 0x10600025, 0x14600025, "Play_Draw: notebook-branch always taken"),
    (0x09946C, 0x240E00F0, 0x240E01C6, "View_Init: viewport bottomY 240->454"),
    (0x099470, 0x240F0140, 0x240F0240, "View_Init: viewport rightX 320->576"),
    # Sram_OpenSave: force the not-owl path and the first-cycle entrance
    # (South Clock Town, day 0, time 6:00-1) so every load - including
    # the auto-confirmed File 1 - starts playable instead of looping
    # through the intro cutscene entrance.
    (0x9F624, 0x156000A2, 0x00000000, "Sram_OpenSave: force not-owl path"),
    (0x9F884, 0x11600006, 0x00000000, "Sram_OpenSave: force first-cycle entrance"),
]


def force_all_a_presses(code):
    """Debug: turn every `andi rt, rs, 0x8000` (A-button checks, 37
    sites incl. the message text-advance) into `ori` so A reads pressed
    everywhere - dialogs and cutscene prompts auto-advance."""
    n = len(code) // 4
    ws = list(struct.unpack_from(">%dI" % n, code, 0))
    count = 0
    for p in range(n):
        w = ws[p]
        if (w >> 26) == 0x0C and (w & 0xFFFF) == 0x8000:
            ws[p] = w | 0x04000000
            count += 1
    _err(f"  A-press forced at {count} andi sites")
    # compression donation: zero the ASCII-to-charcode table head
    # (code+0x133084); ocarina note rendering degrades, fine for a
    # throwaway emulator run
    code[:] = struct.pack(">%dI" % n, *ws)
    donate_at, donate_len = 0x133084, 0x6B
    code[donate_at:donate_at + donate_len] = b"\x00" * donate_len
    _err(f"  compression donation: zeroed {donate_len} bytes at code+{donate_at:06X}")

# Debug (unattended-run) patches inside ovl_file_choose:
# 1. andi-masked press&(START|A) checks -> press|(START|A) (4 sites).
# 2. The nor-folded A/START bit tests in FileSelect_UpdateMainMenu and
#    FileSelect_ConfirmFile forced nonzero, so the occupied-file path
#    auto-selects File 1 (the real save in the .fla) and the confirm
#    dialog answers YES -> SM_FADE_OUT -> FileSelect_LoadGame -> Play.
FS_DBG_EDITS = [
    (0x1B8, 0x31CF9000, 0x35CF9000, "press START|A forced (handler 1)"),
    (0x1D4, 0x304B9000, 0x344B9000, "press START|A forced (handler 1b)"),
    (0x8B4, 0x31CF9000, 0x35CF9000, "press START|A forced (handler 2)"),
    (0x8D0, 0x304B9000, 0x344B9000, "press START|A forced (handler 2b)"),
    (0x83B0, 0x01C17827, 0x240F1000, "UpdateMainMenu: START bit forced"),
    (0x83C8, 0x0041C027, 0x24180001, "UpdateMainMenu: A bit forced"),
    (0x0E848, 0x01C17827, 0x240F1000, "ConfirmFile: START bit forced"),
    (0x0E860, 0x0041C027, 0x24180001, "ConfirmFile: A bit forced -> YES"),
    (0x55D0, 0x00416827, 0x240D1000, "nameset: START bit forced"),
    (0x5920, 0x01E16827, 0x240D8000, "nameset: A bit forced"),
    (0x5B80, 0x10A00053, 0x00000000, "nameset: validName gate NOPed"),
]


def _err(*a):
    print(*a, file=sys.stderr)


def main():
    dbg = "--dbg" in sys.argv
    import os

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "work", "mm",
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

    if dbg:
        force_all_a_presses(code)
        comp = bytes(crunch64.yaz0.compress(bytes(code)))
        slot = CODE_ROM_END - CODE_ROM_START
        if len(comp) > slot:
            raise SystemExit(f"recompressed code {len(comp)} exceeds slot {slot}")
        rom[CODE_ROM_START:CODE_ROM_START + len(comp)] = comp
        _err(f"code recompressed (dbg): {len(comp)} bytes (slot {slot})")
        fs = bytearray(crunch64.yaz0.decompress(bytes(rom[FS_ROM_START:FS_ROM_END])))
        for off, expect, new, note in FS_DBG_EDITS:
            cur = struct.unpack_from(">I", fs, off)[0]
            if cur != expect:
                raise SystemExit(f"MISMATCH at fs+{off:04X}: expect {expect:08X}, found {cur:08X} ({note})")
            struct.pack_into(">I", fs, off, new)
            _err(f"  file_choose +{off:04X}: {expect:08X} -> {new:08X}  {note}")
        fcomp = bytes(crunch64.yaz0.compress(bytes(fs)))
        fslot = FS_ROM_END - FS_ROM_START
        if len(fcomp) > fslot:
            raise SystemExit(f"file_choose recompress {len(fcomp)} exceeds slot {fslot}")
        rom[FS_ROM_START:FS_ROM_START + len(fcomp)] = fcomp
        _err(f"file_choose recompressed: {len(fcomp)} bytes (slot {fslot})")

    sys.stdout.buffer.write(bytes(rom))
    _err("wrote candidate to stdout (ROM CRCs unchanged: patched segments are outside the CIC window)")


if __name__ == "__main__":
    main()
