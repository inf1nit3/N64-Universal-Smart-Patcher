"""
presets.py
Presets profiles for the Universal N64 Smart Patcher.
"""
from dataclasses import dataclass, field
from typing import Dict, List
import n64_core as core


@dataclass
class Preset:
    key: str
    name: str
    description: str
    options: core.PatchOptions
    warnings: List[str] = field(default_factory=list)


PRESETS: Dict[str, Preset] = {
    "crt_authentic": Preset(
        key="crt_authentic",
        name="📺 CRT Authentic",
        description="Preserves N64 VI Anti-Aliasing & Dithering for authentic CRT TV display.",
        options=core.PatchOptions(
            no_aa=False,
            no_dither=False,
            no_divot=False,
            no_gamma=False,
            hires=False,
        ),
        warnings=["Keeps original N64 blur; recommended only for CRT monitors or retro scalers."],
    ),
    "modern_crisp": Preset(
        key="modern_crisp",
        name="✨ Modern Crisp (HD/4K Displays)",
        description="Disables VI Anti-Aliasing and Dithering for sharp polygon edges on modern flat screens.",
        options=core.PatchOptions(
            no_aa=True,
            no_dither=True,
            no_divot=True,
            no_gamma=False,
            hires=False,
        ),
        warnings=[],
    ),
    "modern_4k": Preset(
        key="modern_4k",
        name="🚀 Modern 4K / High-Res (640x480 + No-AA)",
        description="Enables 640x480 Hi-Res VI Mode Table Engine & No-AA for maximum resolution on 4K/FPGA.",
        options=core.PatchOptions(
            no_aa=True,
            no_dither=True,
            no_divot=True,
            no_gamma=False,
            hires=True,
        ),
        warnings=["Requires Expansion Pak (8MB RAM) on real N64 hardware."],
    ),
    "speedrun": Preset(
        key="speedrun",
        name="⚡ Speedrun & Competition Safe",
        description="Minimal non-intrusive patches; preserves standard resolution and logic.",
        options=core.PatchOptions(
            no_aa=True,
            no_dither=False,
            no_divot=False,
            no_gamma=False,
            hires=False,
        ),
        warnings=["Always verify competition rules before using patched ROMs in official leaderboards."],
    ),
}


def list_presets() -> List[Dict[str, str]]:
    """Returns a list of dictionaries with preset keys, names, and descriptions."""
    return [
        {"key": p.key, "name": p.name, "description": p.description}
        for p in PRESETS.values()
    ]


def apply_preset(preset_key: str) -> core.PatchOptions:
    """Returns a FRESH PatchOptions object for the given preset key.

    Never returns the shared preset instance itself - callers may mutate
    the returned options (e.g. CLI flag overrides) without affecting
    later runs."""
    if preset_key in PRESETS:
        src = PRESETS[preset_key].options
        return core.PatchOptions(
            no_aa=src.no_aa,
            no_dither=src.no_dither,
            no_divot=src.no_divot,
            no_gamma=src.no_gamma,
            hires=src.hires,
        )
    return core.PatchOptions()


def get_preset_warnings(preset_key: str) -> List[str]:
    """Returns warning strings associated with the preset key."""
    if preset_key in PRESETS:
        return PRESETS[preset_key].warnings
    return []
