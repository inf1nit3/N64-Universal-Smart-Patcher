"""Locate OoT (US 1.0) hi-res patch sites in the ROM.

Reads the big-endian clean dump from `clean.z64` next to this script
(symlink or copy it there; ROMs never enter the repository).

The decomp (zeldaret/oot, targets this exact revision) says the game's
render pipeline is runtime-width through gScreenWidth/gScreenHeight and
its own ViMode machinery; the patch is therefore a handful of code
immediates:

- ViMode_Init: editState 0->ACTIVE(1), viWidth 320->640, viHeight
  240->480 (480i) or ->240 (240p), loRes 1->0 (and modeN 1->0 for 240p)
- SysCfb_Init: framebuffer size constant 320*240 -> target, 8MB-branch
  fb end 0x80400000 -> 0x80600000 (480i only)
- View_Init: viewport word stores -> full target area

Scans for the compiled store patterns and prints raw instruction words
around each hit; the words get pinned as expected values in makeoot.py
after hand-confirmation.
"""

import struct


def print_words(data, off, before, after, label):
    print(f"--- {label} @ 0x{off:08X} ---")
    for i in range(-before, after):
        o = off + i * 4
        if 0 <= o < len(data):
            w = struct.unpack_from(">I", data, o)[0]
            mark = " <<" if i == 0 else ""
            print(f"  {o:08X}: {w:08X}{mark}")
    print()


def main():
    with open("clean.z64", "rb") as f:
        data = f.read()

    # ViMode_Init: SW 240 (viHeight @0x50) immediately followed by
    # SW 320 (viWidth @0x54)
    print("=== ViMode_Init candidates ===")
    for off in range(0x1000, len(data) - 8, 4):
        w = struct.unpack_from(">I", data, off)[0]
        if (w >> 26) == 0x2B and (w & 0xFFFF) == 0x0050:
            nxt = struct.unpack_from(">I", data, off + 4)[0]
            if (nxt >> 26) == 0x2B and (nxt & 0xFFFF) == 0x0054:
                print_words(data, off, 6, 16, "sw viHeight/viWidth")

    # SysCfb_Init: li 0x12C00 = lui reg, 0x1 + ori reg, 0x2C00
    print("=== SysCfb_Init candidates ===")
    for off in range(0x1000, len(data) - 8, 4):
        w = struct.unpack_from(">I", data, off)[0]
        if (w >> 26) == 0x0F and (w & 0xFFFF) == 0x0001:
            nxt = struct.unpack_from(">I", data, off + 4)[0]
            if (nxt >> 26) == 0x0D and (nxt & 0xFFFF) == 0x2C00:
                print_words(data, off, 4, 10, "li 0x12C00")

    # View_Init: SW 320 @0xC (rightX) near SW 240 (@0x4 bottomY)
    print("=== View_Init candidates ===")
    for off in range(0x1000, len(data) - 32, 4):
        w = struct.unpack_from(">I", data, off)[0]
        if (w >> 26) == 0x2B and (w & 0xFFFF) == 0x000C:
            for i in (1, 2, 3, 4, 5, 6):
                w2 = struct.unpack_from(">I", data, off + i * 4)[0]
                if (w2 >> 26) == 0x2B and (w2 & 0xFFFF) == 0x0004:
                    print_words(data, off, 6, 12, "sw viewport rightX/bottomY")
                    break

    # 8MB framebuffer end: lui reg, 0x8040
    print("=== lui 0x8040 sites ===")
    for off in range(0x1000, len(data) - 8, 4):
        w = struct.unpack_from(">I", data, off)[0]
        if (w >> 26) == 0x0F and (w & 0xFFFF) == 0x8040:
            print_words(data, off, 4, 8, "lui 0x8040")


if __name__ == "__main__":
    main()
