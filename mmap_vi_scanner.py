"""
mmap_vi_scanner.py
Fast memory-mapped VI mode table scanner.
"""
import mmap
import os
from typing import List, Dict, Any
import n64_core as core


def scan_vi_tables_mmap(rom_path: str, width_bytes: bytes = core.WIDTH_320_DATA) -> List[Dict[str, Any]]:
    """
    Uses mmap to efficiently scan large ROM binaries for VI mode tables.
    Returns list of dicts with offset and TV standard.
    """
    tables = []
    if not os.path.isfile(rom_path) or os.path.getsize(rom_path) < 64:
        return tables

    file_size = os.path.getsize(rom_path)
    with open(rom_path, "rb") as f:
        try:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                pos = 0
                while True:
                    pos = mm.find(width_bytes, pos)
                    if pos == -1 or pos + 8 > file_size:
                        break
                    next_4 = mm[pos+4:pos+8]
                    if next_4 in core.ALL_BURSTS:
                        tv = (
                            "NTSC" if next_4 == core.NTSC_BURST
                            else ("PAL" if next_4 == core.PAL_BURST else "M-PAL")
                        )
                        tables.append({"offset": pos, "tv": tv})
                    pos += 4
        except Exception as e:
            # Fallback to in-memory bytearray scan if mmap fails
            with open(rom_path, "rb") as f_in:
                return core.find_vi_tables(f_in.read(), width_bytes)

    return tables
