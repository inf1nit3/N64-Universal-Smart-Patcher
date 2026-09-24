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


JR_RA = 0x03E00008


def enclosing_function(data, off, limit=0x2000):
    """[start, end) of the function around `off`: from just past the
    previous `jr ra` and its delay slot to just past the next one."""
    start = off
    while start > max(0, off - limit):
        if struct.unpack_from(">I", data, start - 4)[0] == JR_RA:
            start += 4  # the delay slot belongs to the previous function
            break
        start -= 4
    end = off
    while end < min(len(data) - 4, off + limit):
        if struct.unpack_from(">I", data, end)[0] == JR_RA:
            end += 8
            break
        end += 4
    return start, end


def copy_mode_steps(data, lo, hi):
    """Offsets of `lui r, 0x1000 ; ... ; ori r, r, 0x0400` in [lo, hi): a
    texture-rectangle step word of dsdx 4.0 / dtdy 1.0, the signature of
    the RDP's COPY cycle type.

    Copy mode moves four texels per clock and cannot scale - dsdx must
    stay 4.0 and the rectangle end is inclusive - so this module's
    transform (double the shift, halve the step) is invalid for any
    emitter that draws in it. That is exactly what broke SM64's HUD; see
    scripts/sm64_hires/README.md, "The HUD". On SM64 this test separates
    the three HUD emitters from the four menu emitters without a miss.
    """
    hits = []
    for o in range(lo, hi - 4, 4):
        w = struct.unpack_from(">I", data, o)[0]
        if (w >> 26) != 0x0F or (w & 0xFFFF) != 0x1000:
            continue
        rt = (w >> 16) & 31
        for p in range(o + 4, min(hi, o + 0x24), 4):
            x = struct.unpack_from(">I", data, p)[0]
            if (x >> 26) == 0x0D and ((x >> 21) & 31) == rt and ((x >> 16) & 31) == rt:
                if x & 0xFFFF == 0x0400:
                    hits.append(o)
                break
    return hits


def in_copy_mode_function(data, off):
    """The copy-mode step words of the function `off` sits in (empty when
    the function draws nothing in COPY mode)."""
    lo, hi = enclosing_function(data, off)
    return copy_mode_steps(data, lo, hi)


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
    copy = ap.add_mutually_exclusive_group()
    copy.add_argument(
        "--skip-copy-mode",
        action="store_true",
        help="leave out sites in functions that draw in COPY mode (reported), build the rest",
    )
    copy.add_argument(
        "--allow-copy-mode",
        action="store_true",
        help="edit COPY-mode sites anyway - an experiment, not a fix",
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

    # The transform is invalid in COPY mode (see copy_mode_steps). Refuse
    # by default: a variant that silently includes such sites costs a
    # hardware round and shows garbled glyphs, not an answer.
    copy_sites = {
        off: steps
        for off in [*coord_group, *step_group]
        if (steps := in_copy_mode_function(data, off))
    }
    if copy_sites and not args.allow_copy_mode:
        listing = "\n".join(
            f"  {off:08X}  (copy-mode step word at {', '.join(f'{s:08X}' for s in steps)})"
            for off, steps in sorted(copy_sites.items())
        )
        if not args.skip_copy_mode:
            raise SystemExit(
                f"{len(copy_sites)} requested site(s) sit in functions that draw in the RDP's "
                f"COPY mode, where doubling the shift and halving the step is invalid:\n"
                f"{listing}\n"
                "Pass --skip-copy-mode to build the rest, or --allow-copy-mode to force it. "
                "Background: scripts/sm64_hires/README.md, 'The HUD'."
            )
        print(f"skipping {len(copy_sites)} COPY-mode site(s):\n{listing}")
        coord_group = [o for o in coord_group if o not in copy_sites]
        step_group = [o for o in step_group if o not in copy_sites]

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
