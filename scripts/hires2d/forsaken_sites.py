"""Forsaken 64 (USA) hi-res image: 2D emitter site table.

Image: the recipe's delta applied to the clean 9E330C01 dump.
find2d.py reports a single coordinate packer, unchanged. One site
is barely a 2D layer - kept on record, but expectation is that
Forsaken draws its HUD by another idiom this signature cannot see.

Steps listed are ori/lui immediates of the s5.10 step class
within +-64 words of a packer - a heuristic, kept as a
separate bisect axis.

NOTHING HERE IS VERIFIED ON HARDWARE. See this directory's
README for the bisect protocol.
"""

GROUPS = {
    "all": [
        0x0001A218,
    ],
    "001": [
        0x0001A218,
    ],
}

STEPS = {
    "all_steps": [],
}
