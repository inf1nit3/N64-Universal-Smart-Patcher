"""Fix SM64's HUD on the SubDrag 640x480 image - two ways.

Why the first attempt failed. The three HUD emitters (sites 0, 5, 6 in
makefix.SITES) load a texture step of dsdx = 4.0 - the signature of the
RDP's COPY cycle type, which the display list confirms: the segment-2 list
H2X's source calls `dl_hud_img_begin` sets `G_CYC_COPY`. Copy mode moves
four texels per clock and cannot scale; dsdx must stay 4.0 and the
rectangle's end coordinate is inclusive (hence `x + 15` for a 16-pixel
glyph). makefix's transform - double the shift, halve the step - is right
for the four 1-cycle menu emitters (dsdx 1.0) and meaningless in copy
mode. Halving 4.0 to 2.0 there is what made the HUD's numbers vanish.

--mode copy: keep copy mode, keep the glyph at 1:1, double only its
position. The rectangle becomes [2x, 2x + size - 1], i.e. in 10.2

    start:  x << 3                     (was x << 2)
    end:    (x << 3) + 4 * (size - 1)  (was (x + size - 1) << 2)

The HUD lands where it belongs, glyphs at half size with gaps between
them ("1 5" for 15). Code edits only; cannot break the way the first
attempt did.

--mode full: H2X's route. Switch the HUD to the 1-cycle pipeline, where
the RDP can scale: end coordinates become exclusive and the step 0.5.

    start:  x << 3                     (unchanged from copy mode)
    end:    (x << 3) + 8 * size        (= (2x + 2*size) << 2)
    dsdx, dtdy: 4.0, 1.0  ->  0.5, 0.5

That also needs the display lists around the HUD changed - cycle type,
combiner, filter, render mode - and those live in segment 2, which is
MIO0-compressed. H2X adds two commands to each list; that is impossible in
place, because every later segment-2 address would move. Instead, cycle
type, texture perspective and texture filter all sit in SETOTHERMODE_H
bits 12..21, so one command with shift 12, length 10 sets all three (and
the LUT/LOD/detail fields between them, to their defaults). That frees
exactly the two slots the combiner needs. The combiner and render-mode
words are copied from H2X's compiled ROM, not derived.

The recompressed segment must fit the original slot. It does, with a few
bytes to spare - checked on every build.

Every edit states the word it expects. A mismatch aborts.

Usage:
    python make_hud.py --check HIRES.z64                     # verify, both modes
    python make_hud.py --mode full --rom HIRES.z64 OUT.z64   # + menu fix, CRC restamped
    python make_hud.py --mode full --ips OUT.ips --check HIRES.z64
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MENU_IPS = os.path.normpath(
    os.path.join(HERE, "..", "..", "src", "n64patcher", "game_fixes", "635A2BFF_sm64_menu_2x.ips")
)

# (makefix site index, what it draws, glyph size in pixels,
#  start-coordinate shifts, end-coordinate (addiu, sll) pairs, (dsdx, dtdy))
HUD_SITES = [
    (
        0,
        "print.c render_textrect - HUD font: coins, stars, lives, timer",
        16,
        [0x091BF4, 0x091BFC],
        [(0x091BBC, 0x091BC0), (0x091BC8, 0x091BD0)],
        (0x091C64, 0x091C68),
    ),
    (
        5,
        "hud.c render_hud_tex_lut - 16x16 digits and icons",
        16,
        [0x09DDC8, 0x09DDD4],
        [(0x09DD98, 0x09DD9C), (0x09DDA4, 0x09DDAC)],
        (0x09DE3C, 0x09DE40),
    ),
    (
        6,
        "hud.c render_hud_small_tex_lut - 8x8 glyphs",
        8,
        [0x09E024, 0x09E030],
        [(0x09DFF4, 0x09DFF8), (0x09E000, 0x09E008)],
        (0x09E098, 0x09E09C),
    ),
]

# The words the SubDrag 640x480 image carries at every code offset touched
# here, read from hires.z64 (SHA-1 014832d0...) and cross-checked by
# disassembly. Anything else at these offsets aborts the build.
EXPECTED = {
    0x091BBC: 0x25F8000F,  # addiu t8, t7, 15
    0x091BC0: 0x0018C880,  # sll   t9, t8, 2
    0x091BC8: 0x256C000F,  # addiu t4, t3, 15
    0x091BD0: 0x000C6880,  # sll   t5, t4, 2
    0x091BF4: 0x00194880,  # sll   t1, t9, 2
    0x091BFC: 0x000C6880,  # sll   t5, t4, 2
    0x091C64: 0x3C0F1000,  # lui   t7, 0x1000      dsdx 4.0
    0x091C68: 0x35EF0400,  # ori   t7, t7, 0x0400  dtdy 1.0
    0x09DD98: 0x248C000F,  # addiu t4, a0, 15
    0x09DD9C: 0x000C6880,  # sll   t5, t4, 2
    0x09DDA4: 0x24B9000F,  # addiu t9, a1, 15
    0x09DDAC: 0x00194080,  # sll   t0, t9, 2
    0x09DDC8: 0x00046080,  # sll   t4, a0, 2
    0x09DDD4: 0x00057880,  # sll   t7, a1, 2
    0x09DE3C: 0x3C181000,  # lui   t8, 0x1000
    0x09DE40: 0x37180400,  # ori   t8, t8, 0x0400
    0x09DFF4: 0x248A0007,  # addiu t2, a0, 7
    0x09DFF8: 0x000A5880,  # sll   t3, t2, 2
    0x09E000: 0x24AF0007,  # addiu t7, a1, 7
    0x09E008: 0x000FC080,  # sll   t8, t7, 2
    0x09E024: 0x00045080,  # sll   t2, a0, 2
    0x09E030: 0x00056880,  # sll   t5, a1, 2
    0x09E098: 0x3C0E1000,  # lui   t6, 0x1000
    0x09E09C: 0x35CE0400,  # ori   t6, t6, 0x0400
}

# Segment 2: ROM slot of the MIO0 block, and the two display lists at
# their offsets in the decompressed segment.
SEG2_START, SEG2_END = 0x108A40, 0x114750
DL_BEGIN, DL_END = 0x11AC0, 0x11B28

PIPE_SYNC = (0xE7000000, 0x00000000)
END_DL = (0xB8000000, 0x00000000)
DL_BEGIN_OLD = [
    PIPE_SYNC,
    (0xBA001402, 0x00200000),  # SetCycleType(G_CYC_COPY)
    (0xBA001301, 0x00000000),  # SetTexturePersp(G_TP_NONE)
    (0xB9000002, 0x00000001),  # SetAlphaCompare(G_AC_THRESHOLD)
    (0xF9000000, 0xFFFFFFFF),  # SetBlendColor(255, 255, 255, 255)
    (0xB900031D, 0x005041C8),  # SetRenderMode(G_RM_AA_XLU_SURF, ..2)
    END_DL,
]
DL_BEGIN_NEW = [
    PIPE_SYNC,
    (0xBA000C0A, 0x00000000),  # OtherMode_H 12..21: 1CYCLE, TP_NONE, TF_POINT
    (0xFCFFFFFF, 0xFFFCF279),  # SetCombineMode(G_CC_DECALRGBA x2)   [H2X]
    (0xB9000002, 0x00000001),  # SetAlphaCompare(G_AC_THRESHOLD)
    (0xF9000000, 0xFFFFFFFF),  # SetBlendColor(255, 255, 255, 255)
    (0xB900031D, 0x0F0A7008),  # SetRenderMode(G_RM_TEX_EDGE, ..2)   [H2X]
    END_DL,
]
DL_END_OLD = [
    PIPE_SYNC,
    (0xBA001301, 0x00080000),  # SetTexturePersp(G_TP_PERSP)
    (0xB900031D, 0x00552078),  # SetRenderMode(G_RM_AA_ZB_OPA_SURF, ..2)
    (0xB9000002, 0x00000000),  # SetAlphaCompare(G_AC_NONE)
    (0xBA001402, 0x00000000),  # SetCycleType(G_CYC_1CYCLE)
    (0xBB000000, 0xFFFFFFFF),  # SPTexture(off)
    END_DL,
]
DL_END_NEW = [
    PIPE_SYNC,
    (0xBA000C0A, 0x00082000),  # OtherMode_H 12..21: 1CYCLE, TP_PERSP, TF_BILERP
    (0xB900031D, 0x00552078),  # SetRenderMode(G_RM_AA_ZB_OPA_SURF, ..2)
    (0xB9000002, 0x00000000),  # SetAlphaCompare(G_AC_NONE)
    (0xFCFFFFFF, 0xFFFE793C),  # SetCombineMode(G_CC_SHADE x2)       [H2X]
    (0xBB000000, 0xFFFFFFFF),  # SPTexture(off)
    END_DL,
]

MODES = ("copy", "full")


def sll(rd, rt, sa):
    return (rt << 16) | (rd << 11) | (sa << 6)


def addiu(rt, rs, imm):
    return (0x09 << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def decode_sll(word):
    if (word >> 26) != 0 or (word & 63) != 0:
        raise SystemExit(f"not an sll: {word:08X}")
    return (word >> 11) & 31, (word >> 16) & 31, (word >> 6) & 31  # rd, rt, sa


def decode_addiu(word):
    if (word >> 26) != 0x09:
        raise SystemExit(f"not an addiu: {word:08X}")
    return (word >> 16) & 31, (word >> 21) & 31, word & 0xFFFF  # rt, rs, imm


def reads_writes(word):
    """(registers read, registers written) for the forms in these
    functions. Anything else returns None, and the liveness check treats
    that as a failure rather than a guess."""
    op = word >> 26
    rs, rt, rd = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    if word == 0:
        return set(), set()
    if op == 0:
        funct = word & 63
        if funct == 0:  # sll
            return {rt}, {rd}
        if funct in (0x21, 0x24, 0x25):  # addu, and, or
            return {rs, rt}, {rd}
        if funct == 0x08:  # jr
            return {rs}, set()
        return None
    if op in (0x09, 0x0C, 0x0D):  # addiu, andi, ori
        return {rs}, {rt}
    if op == 0x0F:  # lui
        return set(), {rt}
    if op == 0x23:  # lw
        return {rs}, {rt}
    if op == 0x2B:  # sw
        return {rs, rt}, set()
    return None


def check_dead(data, after, reg, limit=0x80):
    """The register the original addiu wrote must not be read again
    before something overwrites it."""
    for off in range(after + 4, after + limit, 4):
        word = struct.unpack_from(">I", data, off)[0]
        rw = reads_writes(word)
        if rw is None:
            raise SystemExit(f"liveness: unknown instruction {word:08X} at {off:06X}")
        reads, writes = rw
        if reg in reads:
            raise SystemExit(f"liveness: r{reg} is still read at {off:06X} - rewrite unsafe")
        if reg in writes:
            return off
    raise SystemExit(f"liveness: r{reg} never overwritten within {limit:#x} bytes of {after:06X}")


def check_between(data, first, second, reg):
    """The new code writes `reg` one slot early (at the addiu's position),
    so nothing between the two slots may touch it."""
    for off in range(first + 4, second, 4):
        word = struct.unpack_from(">I", data, off)[0]
        rw = reads_writes(word)
        if rw is None or reg in rw[0] or reg in rw[1]:
            raise SystemExit(f"r{reg} touched between {first:06X} and {second:06X} at {off:06X}")


def check_expected(data):
    for off, want in EXPECTED.items():
        got = struct.unpack_from(">I", data, off)[0]
        if got != want:
            raise SystemExit(f"{off:06X}: expected {want:08X}, found {got:08X} - wrong base image")


def plan(data, mode):
    """[(offset, old, new, what)] for the code edits, all checks run."""
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}")
    check_expected(data)
    edits = []
    for _index, _note, size, starts, pairs, steps in HUD_SITES:
        for off in starts:
            old = EXPECTED[off]
            rd, rt, sa = decode_sll(old)
            if sa != 2:
                raise SystemExit(f"{off:06X}: shift is {sa}, not 2")
            edits.append((off, old, sll(rd, rt, 3), f"start x << 2 -> << 3 (r{rd})"))
        # copy mode: inclusive end, 1:1 glyph.  1-cycle: exclusive end, 2x glyph.
        end_add = 4 * (size - 1) if mode == "copy" else 8 * size
        for a_off, s_off in pairs:
            a_old, s_old = EXPECTED[a_off], EXPECTED[s_off]
            tmp, src, imm = decode_addiu(a_old)
            dst, s_src, sa = decode_sll(s_old)
            if imm != size - 1 or s_src != tmp or sa != 2:
                raise SystemExit(f"{a_off:06X}/{s_off:06X}: not the `+{size - 1} ; << 2` pair")
            check_between(data, a_off, s_off, dst)
            check_dead(data, s_off, tmp)
            edits.append((a_off, a_old, sll(dst, src, 3), f"end: r{dst} = x << 3"))
            edits.append((s_off, s_old, addiu(dst, dst, end_add), f"end: r{dst} += {end_add}"))
        if mode == "full":
            dsdx_off, dtdy_off = steps
            lui_old, ori_old = EXPECTED[dsdx_off], EXPECTED[dtdy_off]
            if lui_old & 0xFFFF != 0x1000 or ori_old & 0xFFFF != 0x0400:
                raise SystemExit(f"{dsdx_off:06X}: not the copy-mode step 4.0 / 1.0")
            lui_new = (lui_old & 0xFFFF0000) | 0x0200
            ori_new = (ori_old & 0xFFFF0000) | 0x0200
            edits.append((dsdx_off, lui_old, lui_new, "dsdx 4.0 -> 0.5"))
            edits.append((dtdy_off, ori_old, ori_new, "dtdy 1.0 -> 0.5"))
    return edits


def segment2_blob(data):
    """The recompressed segment 2 with both display lists rewritten, as
    it goes into the ROM at SEG2_START. Checked against the slot."""
    import crunch64  # only full mode needs it

    if data[SEG2_START : SEG2_START + 4] != b"MIO0":
        raise SystemExit(f"no MIO0 block at {SEG2_START:06X}")
    seg = bytearray(crunch64.mio0.decompress(bytes(data[SEG2_START:SEG2_END])))
    for base, old, new in (
        (DL_BEGIN, DL_BEGIN_OLD, DL_BEGIN_NEW),
        (DL_END, DL_END_OLD, DL_END_NEW),
    ):
        if len(old) != len(new):
            raise SystemExit("a display list may not change length in place")
        for i, words in enumerate(old):
            got = struct.unpack_from(">II", seg, base + 8 * i)
            if got != words:
                raise SystemExit(
                    f"segment 2 +{base + 8 * i:05X}: expected {words[0]:08X} {words[1]:08X}, "
                    f"found {got[0]:08X} {got[1]:08X}"
                )
        for i, (w0, w1) in enumerate(new):
            struct.pack_into(">II", seg, base + 8 * i, w0, w1)
    blob = crunch64.mio0.compress(bytes(seg))
    if crunch64.mio0.decompress(blob) != bytes(seg):
        raise SystemExit("MIO0 round-trip mismatch")
    slot = SEG2_END - SEG2_START
    if len(blob) > slot:
        raise SystemExit(f"segment 2 recompresses to {len(blob)} bytes, slot is {slot}")
    return blob, slot


def read_ips(path):
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:5] != b"PATCH" or blob[-3:] != b"EOF":
        raise SystemExit(f"{path}: not a plain IPS")
    records, pos = [], 5
    while pos < len(blob) - 3:
        off = int.from_bytes(blob[pos : pos + 3], "big")
        size = int.from_bytes(blob[pos + 3 : pos + 5], "big")
        if size == 0:
            raise SystemExit(f"{path}: RLE records are not expected here")
        records.append((off, blob[pos + 5 : pos + 5 + size]))
        pos += 5 + size
    return records


def write_ips(path, records):
    out = bytearray(b"PATCH")
    for off, data in sorted(records):
        # IPS records top out at 0xFFFF bytes; split anything larger.
        for i in range(0, len(data), 0xFFFF):
            chunk = data[i : i + 0xFFFF]
            out += (off + i).to_bytes(3, "big") + len(chunk).to_bytes(2, "big") + chunk
    out += b"EOF"
    with open(path, "wb") as f:
        f.write(out)
    return len(out)


def build_records(data, mode, with_menu=True):
    """Every (offset, bytes) record the fix consists of."""
    edits = plan(data, mode)
    records = [(off, struct.pack(">I", new)) for off, _old, new, _w in edits]
    notes = [f"{len(edits)} code words"]
    if mode == "full":
        blob, slot = segment2_blob(data)
        records.append((SEG2_START, blob))
        notes.append(f"segment 2 {len(blob)}/{slot} B")
    if with_menu:
        menu = read_ips(MENU_IPS)
        records = menu + records
        notes.append(f"{len(menu)} menu records")
    return edits, records, ", ".join(notes)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=MODES, default="full")
    parser.add_argument("--check", metavar="HIRES", help="verify both modes against an image")
    parser.add_argument("--rom", nargs=2, metavar=("HIRES", "OUT"), help="build a test ROM")
    parser.add_argument("--ips", metavar="OUT", help="write menu fix + HUD as one IPS")
    parser.add_argument("--no-menu", action="store_true", help="HUD edits only")
    args = parser.parse_args()

    base = args.check or (args.rom[0] if args.rom else None)
    if base is None:
        parser.error("needs --check HIRES.z64 or --rom HIRES.z64 OUT.z64")
    with open(base, "rb") as f:
        data = bytearray(f.read())

    if args.check and not (args.rom or args.ips):
        for mode in MODES:
            _edits, _records, notes = build_records(data, mode, not args.no_menu)
            print(f"{mode:4s}: all checks passed - {notes}")
        return

    edits, records, notes = build_records(data, args.mode, not args.no_menu)
    for off, old, new, what in edits:
        print(f"  {off:06X}  {old:08X} -> {new:08X}  {what}")
    print(f"mode {args.mode}: {notes}")

    if args.rom:
        for off, blob in records:
            data[off : off + len(blob)] = blob
        sys.path.insert(0, HERE)
        import makefix  # the project CRC engine lives behind it

        out, crc1, crc2 = makefix.crc_fix(bytes(data))
        with open(args.rom[1], "wb") as f:
            f.write(out)
        print(f"wrote {args.rom[1]}  CRC1 {crc1:08X}  CRC2 {crc2:08X}")
    if args.ips:
        size = write_ips(args.ips, records)
        print(f"wrote {args.ips} ({size} bytes)")


if __name__ == "__main__":
    main()
