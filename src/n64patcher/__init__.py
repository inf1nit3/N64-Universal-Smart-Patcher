"""Universal N64 ROM Inspector & Smart Patcher.

The engine is importable without any third-party dependency:

    from n64patcher import inspect_rom_details, patch_rom, PatchOptions

PyQt6 (GUI) and py7zr (.7z archives) are optional extras; nothing in the
core import path touches them.
"""

from ._version import __version__
from .n64_core import (
    PatchOptions,
    build_output_path,
    calculate_n64_crc,
    check_tools,
    detect_cic_chip,
    detect_format,
    ensure_z64,
    export_report,
    fix_rom_crc_native,
    inspect_rom_details,
    patch_rom,
)

__all__ = [
    "PatchOptions",
    "__version__",
    "build_output_path",
    "calculate_n64_crc",
    "check_tools",
    "detect_cic_chip",
    "detect_format",
    "ensure_z64",
    "export_report",
    "fix_rom_crc_native",
    "inspect_rom_details",
    "patch_rom",
]
