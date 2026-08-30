"""Drive the full per-game 2D analysis from one clean dump.

Does what the GoldenEye analysis did interactively, as one command:

1. resolve the dump's CRC1/CRC2 and the matching recipe;
2. normalise to big-endian and apply the recipe's .xdelta with the
   built-in VCDIFF engine -> the hi-res image (work/<key>/hires.z64);
3. survey the hi-res image with find2d and compare every coordinate
   packer against the clean dump - packers the delta left alone are the
   game's structural 320-space gap;
4. print a draft site file (all groups, one group per code region).

Usage:
    python analyze.py "path/to/rom.[zv n]64"
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
from find2d import find_shift_packers  # noqa: E402

WORKROOT = os.path.normpath(os.path.join(HERE, "..", "..", "work"))


def region_of(off):
    """Bucket a site into a code-region group by 0x10000 granularity."""
    return f"r_{off >> 16:03X}xxx"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    from n64patcher import n64_core as core
    from n64patcher import xdelta_patch

    with open(src, "rb") as f:
        data = f.read()
    fmt, _label = core.detect_format(data[:64])
    if fmt is None:
        raise SystemExit("not a recognizable N64 dump")
    clean = bytes(data) if fmt == "z64" else core.to_big_endian(data, fmt)
    crc1, crc2 = clean[16:20].hex().upper(), clean[20:24].hex().upper()
    print(f"dump: {os.path.basename(src)}  CRC1 {crc1}  CRC2 {crc2}  {len(clean)} bytes")

    delta = core.get_subdrag_patch(crc1, crc2)
    if delta is None:
        raise SystemExit("no verified recipe for this dump - nothing to analyse")
    print("delta:", os.path.basename(delta))

    key = f"{crc1[:4]}_{os.path.splitext(os.path.basename(src))[0]}".replace(" ", "_")
    workdir = os.path.join(WORKROOT, key)
    os.makedirs(workdir, exist_ok=True)
    clean_path = os.path.join(workdir, "clean.z64")
    hires_path = os.path.join(workdir, "hires.z64")
    if not os.path.exists(clean_path):
        with open(clean_path, "wb") as f:
            f.write(clean)

    res = xdelta_patch.apply_xdelta_patch(clean_path, delta, hires_path)
    if res.get("status") != "patched":
        raise SystemExit(f"delta failed: {res.get('message')}")
    print("hi-res:", res["message"])

    with open(hires_path, "rb") as f:
        hires = f.read()
    packers = find_shift_packers(hires)
    print(f"\ncoordinate packers in the hi-res image: {len(packers)}")

    groups: dict[str, list[int]] = {}
    stale = unchanged = 0
    for off, w, _user in packers:
        if off + 4 > len(clean):
            stale += 1
            continue
        cw = struct.unpack_from(">I", clean, off)[0]
        if cw == w:
            unchanged += 1
            groups.setdefault(region_of(off), []).append(off)
        else:
            stale += 1
    print(f"unchanged from the clean dump: {unchanged} (the delta already moved {stale})")
    if not groups:
        print("no 320-space gap: the delta covers the 2D layer. Nothing to fix here.")
        return

    print("\ndraft site file (review the grouping by hand, then name it):\n")
    print("GROUPS = {")
    print('    "all": [')
    for off in sorted(o for offs in groups.values() for o in offs):
        print(f"        0x{off:08X},")
    print("    ],")
    for name in sorted(groups):
        print(f'    "{name}": [')
        for off in sorted(groups[name]):
            print(f"        0x{off:08X},")
        print("    ],")
    print("}")
    print(
        "\nSTEPS = {}  # fill from find2d context: ori/lui with "
        "0x0400/0x0800/0xFC00 near the blitter"
    )
    print(
        f"\nnext: makefix2d.py {hires_path} out.z64 --group all "
        "--ips bisect/<CRC1>_<game>_bisect.ips --no-crc"
    )


if __name__ == "__main__":
    main()
