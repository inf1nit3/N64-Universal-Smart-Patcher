"""Build a 2D-layer fix candidate for any hi-res N64 image.

The recipe this implements is the one the SM64 analysis proved on
hardware: the 2D layer (menus, HUD, text) draws through RDP rectangle
commands whose screen coordinates are packed into s10.2 fixed point by
`x << 2`; doubling the shift to `x << 3` doubles the coordinate at zero
instruction cost, and halving the texture steps (s5.10 fields, sign
aware) stretches the glyphs instead of repeating them.

Unlike scripts/sm64_hires/makefix.py this needs no per-game code: the
sites are data, found with find2d.py and grouped by hand into a site
file (see ge_sites.py for the format). Every edit states the word it
expects - a mismatch aborts the build rather than writing into whatever
happens to be at that offset.

Usage:
    python makefix2d.py hires.z64 fixed.z64 --group blitter
    python makefix2d.py hires.z64 fixed.z64 --group all --steps group_blitter
    python makefix2d.py hires.z64 --ips out.ips --group blitter   # no CRC stamp
"""

import argparse
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SM64_DIR = os.path.normpath(os.path.join(HERE, "..", "sm64_hires"))
sys.path.insert(0, SM64_DIR)
import makefix  # noqa: E402  (crc_fix is shared, battle-tested on SM64)


def load_site_file(name):
    path = name if os.path.isfile(name) else os.path.join(HERE, name)
    if not path.endswith(".py"):
        path += ".py"
    spec = importlib.util.spec_from_file_location("site_file", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def double_shift(data, off):
    w = struct.unpack_from(">I", data, off)[0]
    if (w >> 26) or (w & 0x3F) or ((w >> 6) & 31) != 2:
        raise SystemExit(f"{off:08X}: {w:08X} is not `sll rd, rt, 2` - site table is stale")
    return w + (1 << 6)


def halve_step_imm(data, off):
    """Halve a signed s5.10 step immediate in an ori or lui instruction.

    A lui carries the high half of a step word (dsdx), an ori the low
    half (dtdy) or a whole step on its own; both halves halve the same
    way once the immediate is read as signed.
    """
    w = struct.unpack_from(">I", data, off)[0]
    if (w >> 26) not in (0x0D, 0x0F):
        raise SystemExit(f"{off:08X}: {w:08X} is not an ori/lui - step table is stale")
    imm = w & 0xFFFF
    signed = imm - 0x10000 if imm & 0x8000 else imm
    if signed % 2:
        raise SystemExit(f"{off:08X}: step {signed} is odd - halving would lose a bit")
    halved = signed // 2
    halved &= 0xFFFF
    return (w & 0xFFFF0000) | halved


def build_ips(edits):
    """edits: {offset: 4-byte replacement} -> plain IPS bytes."""
    hunks = []
    for off in sorted(edits):
        data = edits[off]
        if hunks and hunks[-1][0] + len(hunks[-1][1]) == off:
            hunks[-1][1] += data
        else:
            hunks.append([off, bytearray(data)])
    out = bytearray(b"PATCH")
    for off, data in hunks:
        out += bytes(
            [
                (off >> 16) & 0xFF,
                (off >> 8) & 0xFF,
                off & 0xFF,
                (len(data) >> 8) & 0xFF,
                len(data) & 0xFF,
            ]
        )
        out += data
    out += b"EOF"
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="the hi-res (patched) .z64 to build on")
    ap.add_argument("output", nargs="?", help="edited image out (default: none)")
    ap.add_argument("--sites", default="ge_sites.py", help="site file (name in this dir or path)")
    ap.add_argument("--group", required=True, help="coordinate site group from the site file")
    ap.add_argument("--steps", metavar="GROUP", help="also halve the step immediates in this group")
    ap.add_argument(
        "--ips",
        metavar="PATH",
        help="also write the edits as an IPS (offsets are hi-res-image based, as Stage 1b expects)",
    )
    ap.add_argument(
        "--no-crc",
        action="store_true",
        help="skip the boot checksum restamp (IPS route: the pipeline restamps anyway)",
    )
    args = ap.parse_args()

    sites = load_site_file(args.sites)
    if args.group not in sites.GROUPS:
        raise SystemExit(
            f"no group {args.group!r} in {args.sites}; have: {', '.join(sites.GROUPS)}"
        )
    coord_group = sites.GROUPS[args.group]
    step_group = sites.STEPS.get(args.steps, []) if args.steps else []

    with open(args.image, "rb") as f:
        data = bytearray(f.read())

    edits = {}
    for off in coord_group:
        new = double_shift(data, off)
        edits[off] = struct.pack(">I", new)
        print(f"coord {off:08X}  <<2 -> <<3")
    for off in step_group:
        new = halve_step_imm(data, off)
        edits[off] = struct.pack(">I", new)
        old = struct.unpack_from(">I", data, off)[0]
        print(f"step {off:08X}  {old & 0xFFFF:04X} -> {new & 0xFFFF:04X}")

    if args.ips:
        with open(args.ips, "wb") as f:
            f.write(build_ips(edits))
        print(f"ips: {args.ips} ({len(edits)} words)")

    if not args.output:
        return
    for off, word in edits.items():
        data[off : off + 4] = word
    if not args.no_crc:
        data, crc1, crc2 = makefix.crc_fix(bytes(data))
        print(f"CRC1 {crc1:08X}  CRC2 {crc2:08X}")
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"wrote {args.output} ({len(edits)} words edited)")


if __name__ == "__main__":
    main()
