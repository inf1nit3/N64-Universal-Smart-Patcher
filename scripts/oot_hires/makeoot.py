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
(flavor.txt: 480i, 240p, vitables, 640p or 640pdbg; ROMs never enter the
repository). `640pdbg` is the emulator-test variant of `640p`: it also
forces the title screen's START checks true so `mupen64plus --testshots`
runs unattended into File Select.

Usage: python makeoot.py > cand_oot.z64
"""
import struct
import sys

import crunch64

FLAVORS = ("480i", "240p", "vitables", "640p", "640pdbg")

CODE_VRAM = 0x800110A0
CODE_PSTART, CODE_PEND = 0xA62840, 0xAFD890
BOOT_TABLES = (0x6FC0, 0x7010)  # osViModeNtscLan1 / osViModeMpalLan1


def _err(*a):
    print(*a, file=sys.stderr)


def find_seq(data, words, start=0x1000):
    blob = b"".join(struct.pack(">I", w) for w in words)
    return data.find(blob, start)


def find_en_mag(rom):
    """Locate the ovl_En_Mag file via the dmadata table (0x7430, 1526 entries)."""
    for k in range(1526):
        vs, ve, rs, re_ = struct.unpack_from(">IIII", rom, 0x7430 + k * 16)
        if vs == 0xE6C0D0 and rom[rs:rs + 4] == b"Yaz0":
            return rs, re_
    raise SystemExit("ovl_En_Mag not found in dmadata")


FILESELECT_VROM = 0xBA12C0
FILESELECT_VRAM = 0x80803880

# HUD x-position registers (640/320 = 2x scale). Each entry is
# (sh byte-offset, compile-time value); the sh offset pins down the
# register. The runtime reg-data pointer is gRegEditor->data + 0x14
# (verified: C_UP_BTN_X=254 stores at 0x810, not 0x7FC), so every
# offset here is the regs.h byte offset + 0x14:
#   ZREG(r) = data[960+r], XREG(r) = data[1344+r], VREG(r) = data[1920+r]
# (regs.h byte offset = index * 2; values from include/interface.h via
# z_construct.c's Regs_InitDataImpl / Interface_Init assignments).
# R_ITEM_BTN_X(0) / R_ITEM_ICON_X(0) / R_START_BTN_X are covered by
# HUD_EXTRA_SITES below (their li serves two stores or sits far away).
HUD_SCALE_EDITS = [
    (0x822, 227),  # R_ITEM_BTN_X(1)  = C_LEFT_BUTTON_X
    (0x824, 249),  # R_ITEM_BTN_X(2)  = C_DOWN_BUTTON_X
    (0x826, 271),  # R_ITEM_BTN_X(3)  = C_RIGHT_BUTTON_X
    (0x838, 160),  # R_ITEM_ICON_X(0) = B_BUTTON_X (Interface_Init li)
    (0x83A, 227),  # R_ITEM_ICON_X(1) = C_LEFT_BUTTON_X
    (0x83C, 249),  # R_ITEM_ICON_X(2) = C_DOWN_BUTTON_X
    (0x83E, 271),  # R_ITEM_ICON_X(3) = C_RIGHT_BUTTON_X
    (0xF94, 162),  # R_ITEM_AMMO_X(0) = B_BUTTON_X + 2
    (0xF96, 228),  # R_ITEM_AMMO_X(1) = C_LEFT_BUTTON_X + 1
    (0xF98, 250),  # R_ITEM_AMMO_X(2) = C_DOWN_BUTTON_X + 1
    (0xF9A, 272),  # R_ITEM_AMMO_X(3) = C_RIGHT_BUTTON_X + 1
    (0xAB6, 186),  # R_A_BTN_X        = A_BUTTON_X
    (0xAC4, 186),  # R_A_ICON_X       = A_BUTTON_X
    (0x810, 254),  # R_C_UP_BTN_X     = C_UP_BUTTON_X
    (0x844, 247),  # R_C_UP_ICON_X    = C_UP_BUTTON_X - 7
    (0xAF6, 18),   # R_MAGIC_METER_X  = 18
]

# Two assignments the generic matcher cannot pin down (their li sits far
# from the store or shares it), patched by exact instruction context
# (file offset, expected word, new immediate):
# - code+0xD0EEC li v0,160: feeds both R_ITEM_BTN_X(0) and
#   R_ITEM_ICON_X(0) stores (sh v0, 0x820/0x838) -> 320
# - code+0xD14EC li t9,132: feeds the R_START_BTN_X store
#   (sh t9, 0x81C) -> 264
# - code+0xD1C40 li a2,250: feeds the R_ITEM_AMMO_X(2) store
#   (sh a2, 0xF98) -> 500 (unique li a2,250 in Regs_InitDataImpl)
HUD_EXTRA_SITES = [
    (0xD0EEC, 0x240200A0, 0x24020140),
    (0xD14EC, 0x24190084, 0x24190108),
    (0xD1C40, 0x240600FA, 0x240601F4),
]


def patch_hud_scale(code):
    """Scale the 320-space HUD x-positions to 640. The registers are
    assigned with `sh rt, off(gRegEditor)` stores whose offset pins down
    the register; the stored value comes from a `li rt2, value` a few
    instructions earlier (sometimes shared between two stores of the
    same value, sometimes moved through registers), so each li feeding a
    matched store is doubled once."""
    n = len(code) // 4
    ws = list(struct.unpack_from(">%dI" % n, code, 0))

    li_patches = {}  # li index -> (old, new)
    for off, old in HUD_SCALE_EDITS:
        new = old * 2
        stores = 0
        for p in range(n):
            y = ws[p]
            if (y >> 26) != 0x29 or (y & 0xFFFF) != off:
                continue
            rt = (y >> 16) & 31  # sh rt, off(rs): rt holds the value
            if rt == 0:
                continue
            stores += 1
            for q in range(max(0, p - 16), p):
                w = ws[q]
                if (w >> 26) in (9, 13) and (w & 0xFFFF) == old and ((w >> 16) & 31) == rt:
                    prev = li_patches.get(q)
                    if prev is None:
                        li_patches[q] = (old, new)
                    elif prev != (old, new):
                        raise SystemExit(
                            f"HUD scale: li at code+{q*4:06X} feeds conflicting scales")
                    break
        if stores == 0:
            _err(f"  HUD scale: WARNING no store for sh {off:#06x} (value {old})")

    for off, expect, new in HUD_EXTRA_SITES:
        cur = ws[off // 4]
        if cur != expect:
            raise SystemExit(
                f"HUD extra site code+{off:06X}: expect {expect:08X}, found {cur:08X}")
        ws[off // 4] = new
        _err(f"  HUD scale: code+{off:06X}: {expect:08X} -> {new:08X} (extra site)")
    for q, (old, new) in sorted(li_patches.items()):
        ws[q] = (ws[q] & 0xFFFF0000) | new
        _err(f"  HUD scale: code+{q*4:06X}: li {old} -> {new}")
    _err(f"  HUD scale: {len(li_patches) + len(HUD_EXTRA_SITES)} li sites patched for {len(HUD_SCALE_EDITS)} registers")
    return struct.pack(">%dI" % n, *ws)


def find_fileselect(rom):
    """Locate ovl_file_choose via the gamestate overlay table in the code
    segment (gGameStateOverlayTable @0x800F1340, FileSelect entry vrom)."""
    for k in range(1526):
        vs, ve, rs, re_ = struct.unpack_from(">IIII", rom, 0x7430 + k * 16)
        if vs == FILESELECT_VROM and rom[rs:rs + 4] == b"Yaz0":
            return rs, re_
    raise SystemExit("ovl_file_choose not found in dmadata")


def patch_fileselect_autoadvance(rom):
    """Debug patch: force the File Select inputs so an unattended run
    registers File 1 ("Link") and launches the game. All sites are
    `andi rX, rY, mask` turned into `ori` (press|mask always equals the
    mask, so CHECK_BTN_ALL passes every frame):

    - FileSelect_UpdateMainMenu 0x8080C2AC/C2C0: START/A on the file
      list (z_file_choose.c "START || A") -> selects File 1
    - nameset keyboard 0x80808DC4: START = END shortcut (z_file_nameset
      line 651); 0x80808FD4/0x80809088/0x80809280/0x80809470: the A
      decides of the keyboard states -> name registers, game loads
    """
    rs, re_ = find_fileselect(rom)
    data = bytearray(crunch64.yaz0.decompress(bytes(rom[rs:re_])))
    sites = [
        (0x8080C2AC, 0x31CF1000, "main menu START"),
        (0x8080C2C0, 0x33198000, "main menu A"),
    ]
    for vaddr, expect, note in sites:
        off = vaddr - FILESELECT_VRAM
        cur = struct.unpack_from(">I", data, off)[0]
        if cur != expect:
            raise SystemExit(
                f"FileSelect MISMATCH at 0x{vaddr:08X}: expect {expect:08X}, found {cur:08X} ({note})")
        struct.pack_into(">I", data, off, expect | 0x04000000)
        _err(f"  FileSelect 0x{vaddr:08X}: {expect:08X} -> {expect | 0x04000000:08X}  {note}")
    # The keyboard state machine stays stuck on an unattended run (the
    # forced presses alone do not walk it to the confirm), so skip it
    # entirely: FileSelect_StartNameEntry jumps straight into
    # FileSelect_LoadGame, which opens File 1 with the default save.
    # (The compressed overlay only just fits its ROM slot, so this
    # replaces the five keyboard `andi->ori` patches used before.)
    # The keyboard state machine stays stuck on an unattended run (the
    # forced presses alone do not walk it to the confirm), so skip it
    # entirely: in sFileSelectUpdateFuncs[] the name-entry mode's
    # FileSelect_StartNameEntry is swapped for FileSelect_LoadGame, which
    # opens File 1 with the default save as soon as that mode starts.
    # (The compressed overlay only just fits its ROM slot, so the patch
    # site matters: a single .data table word compresses smaller than
    # in-place code edits.)
    loadgame = 0x8081117C
    startname = struct.pack(">I", 0x80809B64)
    off = data.find(startname)
    if off < 0 or data.find(startname, off + 1) >= 0:
        raise SystemExit("FileSelect: StartNameEntry table word not unique")
    struct.pack_into(">I", data, off, loadgame)
    _err(f"  FileSelect +{off:05X}: update-func StartNameEntry -> FileSelect_LoadGame")
    comp = bytes(crunch64.yaz0.compress(bytes(data)))
    if len(comp) > re_ - rs:
        raise SystemExit(f"FileSelect recompress {len(comp)} exceeds slot {re_ - rs}")
    rom[rs:rs + len(comp)] = comp
    _err(f"FileSelect recompressed: {len(comp)} bytes (slot {re_ - rs})")


def patch_en_mag_autoadvance(rom):
    """Debug patch: force the title screen's START checks true so the boot
    runs on into File Select without input (mupen --testshots runs). Both
    `andi tX, v1, 0x1000` words become `ori` (press | 0x1000 is always
    equal to the mask, so CHECK_BTN_ALL passes every frame)."""
    rs, re_ = find_en_mag(rom)
    data = bytearray(crunch64.yaz0.decompress(bytes(rom[rs:re_])))
    for expect in (0x30781000, 0x306F1000):  # andi t8,v1,0x1000 / andi t7,v1,0x1000
        off = data.find(struct.pack(">I", expect))
        if off < 0:
            raise SystemExit(f"En_Mag START check {expect:08X} not found")
        struct.pack_into(">I", data, off, expect | 0x04000000)
        _err(f"  En_Mag +{off:04X}: {expect:08X} -> {expect | 0x04000000:08X}  START press forced")
    comp = bytes(crunch64.yaz0.compress(bytes(data)))
    if len(comp) > re_ - rs:
        raise SystemExit(f"En_Mag recompress {len(comp)} exceeds slot {re_ - rs}")
    rom[rs:rs + len(comp)] = comp
    _err(f"En_Mag recompressed: {len(comp)} bytes (slot {re_ - rs})")


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
    # View_Init: the plain li-pair also appears in func_8008A994, so anchor
    # on View_Init's unique 4-word window (lui t8,0x5649 / li t6,240 /
    # li t7,320 / ori t8,t8,0x4557); pair sits at +0x04/+0x08
    view = find_seq(code, [0x3C185649, 0x240E00F0, 0x240F0140, 0x37184557])
    if find_seq(code, [0x3C185649, 0x240E00F0, 0x240F0140, 0x37184557], view + 4) >= 0:
        raise SystemExit("View_Init anchor is not unique")
    # Main(): boot re-assignment gScreenWidth/gScreenHeight = 320/240
    main_gsw = find_seq(code, [0x240E0140, 0x3C018010, 0xAC2EE500])
    # ViMode_Update's gScreenWidth/gScreenHeight = viWidth/viHeight copy
    # (dead on stock NTSC 1.0 - only runs when the SREG VI editor is on;
    # NOPed so the editor can never shrink the width back)
    vimode_nop = find_seq(code, [0x8E0F0054, 0x3C018010, 0xAC2FE500]) + 0x8
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
    # View_Init: +0x04 li t6,240 (bottomY); +0x08 li t7,320 (rightX)
    view_h, view_w = view + 0x04, view + 0x08

    edits = []  # (kind, offset, new, expect, note)
    dbg_autoadvance = flavor == "640pdbg"  # 640p edits + title auto-advance
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
    elif flavor in ("640p", "640pdbg"):
        # 640x240 PROGRESSIVE without the ViMode editor hack (480i is
        # dead per hardware verdict - interlace unusable). Retail VI
        # path: osViSetMode is never re-issued on NTSC 1.0 (viMode stays
        # NULL), so the boot's static osViModeNtscLan1/MpalLan1 tables
        # drive the display the whole session; the tables carry the
        # 640-wide progressive mode. The game renders 640-wide because
        # gScreenWidth's .data init AND Main()'s boot re-assignment are
        # widened (nothing else writes gScreenWidth at runtime); the
        # SysCfb framebuffer pair grows to 2x 640x240 (0x96000 total,
        # +0x4B000 vs stock) and the fb end moves to the Expansion Pak
        # area so the game heap keeps its stock size - REQUIRES 8 MB.
        # View_Init widens the viewport so the 3D fills the framebuffer.
        # Known risk: gZBuffer stays 320x240, so 640-wide scissor
        # overflows 0x25800 bytes into gGfxSPTaskOutputBuffer
        # (survived in mupen64plus; hardware verdict pending).
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
            ("c", vimode_nop, 0x00000000, 0xAC2FE500, "ViMode_Update width copy -> NOP"),
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

    if flavor in ("640p", "640pdbg"):
        code = bytearray(patch_hud_scale(code))
        n += len(HUD_SCALE_EDITS)
        # Compression donation: the JP message table is dead in US
        # retail; zeroing its head gives the recompressor back the bytes
        # the HUD edits cost (the yaz0 stream only just fits the slot).
        donate_at, donate_len = 0xFF8AC, 0x40
        code[donate_at:donate_at + donate_len] = b"\x00" * donate_len
        n += 1
        _err(f"  compression donation: zeroed {donate_len:#x} bytes at code+{donate_at:06X} (dead JP table)")

    if flavor in ("480i", "240p", "640p", "640pdbg"):
        comp = bytes(crunch64.yaz0.compress(bytes(code)))
        if len(comp) > CODE_PEND - CODE_PSTART:
            raise SystemExit(
                f"recompressed code {len(comp)} exceeds slot {CODE_PEND - CODE_PSTART}")
        rom[CODE_PSTART:CODE_PSTART + len(comp)] = comp
        _err(f"code recompressed: {len(comp)} bytes (slot {CODE_PEND - CODE_PSTART})")

    if dbg_autoadvance:
        patch_en_mag_autoadvance(rom)
        patch_fileselect_autoadvance(rom)

    from n64patcher import n64_core as core

    crc = core.calculate_n64_crc(bytes(rom))
    if crc is None:
        raise SystemExit("CIC not identified - refusing to stamp")
    struct.pack_into(">II", rom, 0x10, crc[0], crc[1])

    sys.stdout.buffer.write(bytes(rom))
    _err(f"wrote candidate to stdout ({flavor}, {n} rom-segment edits)")


if __name__ == "__main__":
    main()
