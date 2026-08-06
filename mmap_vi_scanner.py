"""
mmap_vi_scanner.py
Fast memory-mapped VI mode table scanner.

Thin public wrapper around n64_core.scan_vi_tables_file (the scanner
lives in the core module so it can be shared by inspection, patching
and this API without circular imports).
"""
from typing import List, Dict, Any
import n64_core as core


def scan_vi_tables_mmap(rom_path: str,
                        width_bytes: bytes = core.WIDTH_320_DATA) -> List[Dict[str, Any]]:
    """
    Uses mmap to efficiently scan large ROM binaries for VI mode tables.
    The file must be big-endian .z64. Returns a list of dicts with
    'offset' and 'tv' (NTSC/PAL/M-PAL).
    """
    return core.scan_vi_tables_file(rom_path, width_bytes)