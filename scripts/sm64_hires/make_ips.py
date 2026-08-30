"""Build the shippable SM64 game-fix IPS from the hardware-verified .wip BPS.

Why this exists: makefix.py edits a ROM image, but what ships is an IPS
that Stage 1b applies on top of the hi-res image. The .wip BPS in this
folder was produced by the project's own BPS encoder from exactly the
hires.z64 -> hires_fixed.z64 pair that went to hardware (all seven
sites, CRC restamped). That encoder emits only SourceRead and
TargetRead, so every byte it changed appears in the patch as a literal.

An IPS record may carry any length, so the fix ships as exactly those
changed bytes at their exact offsets. Unchanged bytes inside a site's
words are no-ops by definition - applying the result to a hi-res image
reproduces the hardware-verified image byte for byte, without the ROM
(or its unknown register encodings) ever being needed here.

No ROM is needed or distributed. The structural check below refuses to
write anything if the .wip is not exactly makefix.py's edit: every
changed byte must fall inside one of the seven sites' words or the
8-byte CRC restamp at 0x10, every site must contribute, and the per-byte
positions must be ones the two transforms (shamt +1, immediate >> 1)
can actually touch.

Usage:
    python make_ips.py                    # sites 1-4 (menus, verified)
    python make_ips.py --sites 1,2,3,4    # explicit
    python make_ips.py --out PATH         # somewhere else
    python make_ips.py --bisect           # HUD bisect variants (see README)
"""
import argparse
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import makefix  # noqa: E402  (SITES is the shared, checked site table)

WIP_NAME = "635A2BFF_sm64_hud_textrect_2x.bps.wip"
CRC_OFFSET, CRC_LEN = 0x10, 8
DEFAULT_OUT = os.path.join(
    HERE, "..", "..", "src", "n64patcher", "game_fixes",
    "635A2BFF_sm64_menu_2x.ips")
BPS_SOURCE_READ, BPS_TARGET_READ = 0, 1


def parse_bps_literals(patch):
    """Every TargetRead literal run as (output_offset, bytes), plus the
    declared sizes. Raises when the patch does not decode cleanly."""
    if patch[:4] != b"BPS1":
        raise SystemExit("not a BPS patch")
    if len(patch) < 16:
        raise SystemExit("BPS too short")
    if zlib.crc32(patch[:-4]) & 0xFFFFFFFF != \
            struct.unpack("<I", patch[-4:])[0]:
        raise SystemExit("BPS patch corrupt (patch CRC32 mismatch)")

    def vlv(pos):
        value, shift = 0, 1
        while True:
            byte = patch[pos]
            pos += 1
            value += (byte & 0x7F) * shift
            if byte & 0x80:
                return value, pos
            shift <<= 7
            value += shift

    pos = 4
    src_size, pos = vlv(pos)
    dst_size, pos = vlv(pos)
    meta_size, pos = vlv(pos)
    pos += meta_size

    literals = []
    out_pos = 0
    end = len(patch) - 12
    while pos < end:
        data, pos = vlv(pos)
        command, length = data & 3, (data >> 2) + 1
        if command == BPS_TARGET_READ:
            if pos + length > end:
                raise SystemExit("BPS truncated in TargetRead")
            literals.append((out_pos, patch[pos:pos + length]))
            pos += length
        elif command != BPS_SOURCE_READ:
            raise SystemExit(
                f"unexpected BPS action {command} - this decoder matches "
                "the project encoder, not arbitrary patches")
        out_pos += length
    if out_pos != dst_size:
        raise SystemExit("BPS action stream does not cover the target")
    return literals, src_size, dst_size


def site_byte_ranges():
    """{(offset, position-in-word)} for every byte the site edits can
    touch, per site index: coordinate words and texture-step words."""
    ranges = {}
    for index, (_emitter, _note, coords, dsdx, dtdy) in \
            enumerate(makefix.SITES):
        touched = set()
        for off in coords:
            # `sll rd, rt, 2` -> `sll rd, rt, 3` sets bit 6, so only the
            # low half of the word (bytes 2 and 3) can differ.
            touched.update((off + 2, off + 3))
        for off in (dsdx, dtdy):
            # halving the immediate only changes bytes 2 and 3 as well.
            touched.update((off + 2, off + 3))
        ranges[index] = touched
    return ranges


def check_structure(changed):
    """Validate the .wip against makefix.SITES; return the per-site
    changed bytes. See the module docstring for what is checked."""
    allowed = set(range(CRC_OFFSET, CRC_OFFSET + CRC_LEN))
    per_site = {index: set() for index in range(len(makefix.SITES))}
    for index, touched in site_byte_ranges().items():
        per_site[index] = {off for off in changed if off in touched}
        allowed.update(touched)

    stray = set(changed) - allowed
    if stray:
        raise SystemExit(
            "the .wip changes bytes outside makefix.SITES and the CRC "
            "restamp:\n  "
            + ", ".join(f"{o:08X}" for o in sorted(stray)))
    for index, offsets in per_site.items():
        if not offsets:
            raise SystemExit(
                f"site {index} contributes no changed bytes - the .wip "
                "does not carry makefix's full edit")
    # Byte positions the transforms cannot touch rule out a mismatched
    # site table even when every offset happens to line up.
    for off in changed:
        if CRC_OFFSET <= off < CRC_OFFSET + CRC_LEN:
            continue
        if off % 4 < 2:
            raise SystemExit(
                f"changed byte {off:08X} sits in the high half of a word - "
                "neither the shift nor the immediate halving touches that")
    return per_site


def selected_bytes(changed, sites):
    chosen = {}
    for index in sites:
        _emitter, _note, coords, dsdx, dtdy = makefix.SITES[index]
        for off in [*coords, dsdx, dtdy]:
            for i in range(4):
                if off + i in changed:
                    chosen[off + i] = changed[off + i]
    return chosen


def build_ips(chosen):
    """Plain IPS over the changed bytes, contiguous runs merged."""
    hunks = []
    for off in sorted(chosen):
        data = bytes([chosen[off]])
        if hunks and hunks[-1][0] + len(hunks[-1][1]) == off:
            hunks[-1][1] += data
        else:
            hunks.append([off, bytearray(data)])
    out = bytearray(b"PATCH")
    for off, data in hunks:
        out.append((off >> 16) & 0xFF)
        out.append((off >> 8) & 0xFF)
        out.append(off & 0xFF)
        out.append((len(data) >> 8) & 0xFF)
        out.append(len(data) & 0xFF)
        out += data
    out += b"EOF"
    return bytes(out)


BisectPlan = [
    # (file name, sites, what the run decides)
    ("635A2BFF_bisect_A_menus.ips", [1, 2, 3, 4],
     "menus only - the shipped configuration, for comparison"),
    ("635A2BFF_bisect_B_plus_hud_font.ips", [0, 1, 2, 3, 4],
     "+ site 0: do the HUD numbers come back?"),
    ("635A2BFF_bisect_C_plus_hud_lut.ips", [1, 2, 3, 4, 5, 6],
     "+ sites 5,6: do the HUD numbers come back?"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sites", default="1,2,3,4",
                        help="site indexes to include (default: 1,2,3,4)")
    parser.add_argument("--out", default=os.path.normpath(DEFAULT_OUT),
                        help="output .ips path")
    parser.add_argument("--bisect", action="store_true",
                        help="write the HUD bisect variants instead")
    args = parser.parse_args()

    with open(os.path.join(HERE, WIP_NAME), "rb") as f:
        patch = f.read()
    literals, src_size, dst_size = parse_bps_literals(patch)
    if src_size != dst_size:
        raise SystemExit("the .wip changes the ROM size - not a word-edit patch")
    changed = {}
    for off, data in literals:
        for i, byte in enumerate(data):
            changed[off + i] = byte
    check_structure(changed)

    if args.bisect:
        out_dir = os.path.join(HERE, "bisect")
        os.makedirs(out_dir, exist_ok=True)
        for name, sites, note in BisectPlan:
            ips = build_ips(selected_bytes(changed, sites))
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(ips)
            print(f"{name}  sites {','.join(map(str, sites))}  - {note}")
        print(f"\nwrote {len(BisectPlan)} variants to {out_dir}")
        print("install one at a time into ~/.n64patcher/game_fixes/ and run")
        print("the normal --hires pipeline; see the README for the protocol.")
        return

    sites = [int(x) for x in args.sites.split(",") if x != ""]
    for index in sites:
        if not 0 <= index < len(makefix.SITES):
            raise SystemExit(f"site {index} does not exist")
    chosen = selected_bytes(changed, sites)
    ips = build_ips(chosen)
    with open(args.out, "wb") as f:
        f.write(ips)
    print(f"{len(sites)} site(s), {len(chosen)} changed bytes "
          f"-> {args.out} ({len(ips)} bytes)")
    for index in sites:
        emitter, note, _c, _d, _t = makefix.SITES[index]
        print(f"  site {index}  rom {emitter:08X}  {note}")


if __name__ == "__main__":
    main()
