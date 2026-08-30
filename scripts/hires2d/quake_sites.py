"""Quake II (USA) hi-res image: 2D emitter site table.

Image: the recipe's delta applied to the clean BDA8F143 dump.
find2d.py reports 25 coordinate packers, all byte-identical to
the clean ROM - the same 320-space gap as SM64/GoldenEye/F-Zero X.

Steps listed are ori/lui immediates of the s5.10 step class
within +-64 words of a packer - a heuristic, kept as a
separate bisect axis.

NOTHING HERE IS VERIFIED ON HARDWARE. See this directory's
README for the bisect protocol.
"""

GROUPS = {
    "all": [
        0x000077CC,
        0x00007854,
        0x0000793C,
        0x000079C4,
        0x0000D54C,
        0x0000D574,
        0x0000D644,
        0x0000D708,
        0x0000D768,
        0x0000D77C,
        0x0000DB50,
        0x0000DB5C,
        0x0000DB68,
        0x0000DBE8,
        0x0000DD78,
        0x0000DD88,
        0x0000DF48,
        0x0000DF58,
        0x0000E1F4,
        0x00011CBC,
        0x00011CD4,
        0x00012394,
        0x000123AC,
        0x00012AE4,
        0x00057868,
    ],
    "000": [
        0x000077CC,
        0x00007854,
        0x0000793C,
        0x000079C4,
        0x0000D54C,
        0x0000D574,
        0x0000D644,
        0x0000D708,
        0x0000D768,
        0x0000D77C,
        0x0000DB50,
        0x0000DB5C,
        0x0000DB68,
        0x0000DBE8,
        0x0000DD78,
        0x0000DD88,
        0x0000DF48,
        0x0000DF58,
        0x0000E1F4,
    ],
    "001": [
        0x00011CBC,
        0x00011CD4,
        0x00012394,
        0x000123AC,
        0x00012AE4,
    ],
    "005": [
        0x00057868,
    ],
}

STEPS = {
    "all_steps": [
        0x0000779C,
        0x000077A0,
        0x0000790C,
        0x00007910,
        0x0000D72C,
        0x0000D730,
        0x0000DF84,
        0x0000DF88,
        0x0000E1AC,
        0x0000E1EC,
        0x00011DA4,
        0x00012474,
    ],
}
