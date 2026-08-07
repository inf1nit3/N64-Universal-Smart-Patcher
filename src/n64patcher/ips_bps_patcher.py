"""
ips_bps_patcher.py
Applies IPS and BPS community patch files to N64 ROMs.

BPS decoding follows the reference specification by byuu (beat):
variable-length values use the "+= / shift <<= 7 / += shift" scheme,
commands are 0=TargetRead, 1=SourceRead, 2=SourceCopy, 3=TargetCopy,
and the trailing 12 bytes hold CRC32 checksums (source, target, patch)
which are all verified here.
"""
import os
import struct
import zlib
from typing import Any

from . import n64_core as core

IPS_EOF = b"EOF"
BPS_FOOTER_SIZE = 12

# IPS record offsets are 3 bytes, so no record can address past 16 MiB.
# BPS has no such limit; it is only IPS that silently ignores the tail of
# a 32/64 Mbit cartridge dump.
IPS_MAX_ADDRESSABLE = 0x1000000


def detect_patch_type(patch_path: str) -> str:
    """Detects patch format based on extension and magic bytes."""
    ext = os.path.splitext(patch_path)[1].lower().lstrip(".")
    if ext in ("ips", "bps", "ups"):
        return ext

    if os.path.isfile(patch_path):
        with open(patch_path, "rb") as f:
            header = f.read(5)
        if header.startswith(b"PATCH"):
            return "ips"
        elif header.startswith(b"BPS1"):
            return "bps"
        elif header.startswith(b"UPS1"):
            return "ups"

    return "unknown"


def apply_ips_patch(rom_path: str, patch_path: str, output_path: str,
                    require_n64: bool = True) -> dict[str, Any]:
    """Applies a standard IPS patch to the ROM file.

    Unlike BPS, IPS carries no checksum, so nothing in the format itself
    notices a wrong-byte-order source: feeding it a .v64 would write
    byte-swapped garbage and report success. The source is therefore
    format-checked and converted to native big-endian first. Pass
    require_n64=False to run the record decoder on non-ROM data.
    """
    if not os.path.isfile(rom_path) or not os.path.isfile(patch_path):
        return {"status": "error", "message": "Source ROM or patch file missing"}

    try:
        with open(rom_path, "rb") as f:
            rom_data = bytearray(f.read())

        notes = []
        if require_n64:
            fmt, label = core.detect_format(rom_data)
            if fmt is None:
                return {"status": "error",
                        "message": f"Not a recognizable N64 ROM ({label}) - IPS "
                                   f"patches are built against big-endian .z64 dumps"}
            if fmt != "z64":
                rom_data = bytearray(core.to_big_endian(bytes(rom_data), fmt))
                notes.append(f"converted .{fmt} to .z64 first")
            if len(rom_data) > IPS_MAX_ADDRESSABLE:
                notes.append(f"IPS offsets are 3 bytes: nothing past "
                             f"{IPS_MAX_ADDRESSABLE // (1024 * 1024)} MB of this "
                             f"{len(rom_data) // (1024 * 1024)} MB ROM is addressable")

        with open(patch_path, "rb") as f:
            patch_data = f.read()

        if not patch_data.startswith(b"PATCH"):
            return {"status": "error", "message": "Invalid IPS patch header"}

        pos = 5
        records_applied = 0
        end = len(patch_data)

        while pos + 3 <= end:
            if patch_data[pos:pos + 3] == IPS_EOF:
                pos += 3
                break

            if pos + 5 > end:
                return {"status": "error", "message": "IPS patch truncated in record header"}

            offset = (patch_data[pos] << 16) | (patch_data[pos + 1] << 8) | patch_data[pos + 2]
            size = (patch_data[pos + 3] << 8) | patch_data[pos + 4]
            pos += 5

            if size > 0:
                # Normal record
                if pos + size > end:
                    return {"status": "error", "message": "IPS patch truncated in record data"}
                val_bytes = patch_data[pos:pos + size]
                pos += size
            else:
                # RLE record
                if pos + 3 > end:
                    return {"status": "error", "message": "IPS patch truncated in RLE record"}
                rle_size = (patch_data[pos] << 8) | patch_data[pos + 1]
                val_bytes = bytes(patch_data[pos + 2:pos + 3]) * rle_size
                pos += 3

            # Expand rom_data array if needed
            required_len = offset + len(val_bytes)
            if len(rom_data) < required_len:
                rom_data.extend(b"\x00" * (required_len - len(rom_data)))

            rom_data[offset:offset + len(val_bytes)] = val_bytes
            records_applied += 1

        # Optional truncation: 3-byte size directly after the EOF marker
        if pos + 3 == end:
            truncate_size = (patch_data[pos] << 16) | (patch_data[pos + 1] << 8) | patch_data[pos + 2]
            if truncate_size < len(rom_data):
                del rom_data[truncate_size:]

        with open(output_path, "wb") as f:
            f.write(rom_data)

        message = f"IPS patch applied ({records_applied} records)"
        if notes:
            message += " - " + "; ".join(notes)
        return {
            "status": "patched",
            "message": message,
            "output": output_path,
            "warnings": notes,
        }

    except Exception as e:
        return {"status": "error", "message": f"IPS patch error: {e}"}

def _bps_read_vlv(patch_data: bytes, pos: int) -> tuple[int, int]:
    """Decode one BPS variable-length value (byuu's reference scheme).
    Returns (value, new_pos). Raises ValueError on truncated input."""
    data = 0
    shift = 1
    while True:
        if pos >= len(patch_data):
            raise ValueError("BPS patch truncated inside variable-length value")
        x = patch_data[pos]
        pos += 1
        data += (x & 0x7F) * shift
        if x & 0x80:
            break
        shift <<= 7
        data += shift
    return data, pos


def apply_bps_patch(rom_path: str, patch_path: str, output_path: str) -> dict[str, Any]:
    """Applies a BPS patch with full CRC32 verification."""
    if not os.path.isfile(rom_path) or not os.path.isfile(patch_path):
        return {"status": "error", "message": "Source ROM or patch file missing"}

    try:
        with open(rom_path, "rb") as f:
            rom_data = f.read()

        with open(patch_path, "rb") as f:
            patch_data = f.read()

        if not patch_data.startswith(b"BPS1"):
            return {"status": "error", "message": "Invalid BPS patch header"}
        if len(patch_data) < 4 + BPS_FOOTER_SIZE:
            return {"status": "error", "message": "BPS patch too short"}

        # --- Footer CRC32s (little-endian): source, target, patch ---------
        source_crc, target_crc, patch_crc = struct.unpack("<III", patch_data[-BPS_FOOTER_SIZE:])

        if zlib.crc32(patch_data[:-4]) & 0xFFFFFFFF != patch_crc:
            return {"status": "error",
                    "message": "BPS patch file corrupt (patch CRC32 mismatch)"}

        if zlib.crc32(rom_data) & 0xFFFFFFFF != source_crc:
            return {"status": "error",
                    "message": "BPS source CRC32 mismatch - this patch was built "
                               "for a different ROM version"}

        # --- Header: source size, target size, metadata -------------------
        pos = 4
        src_size, pos = _bps_read_vlv(patch_data, pos)
        dst_size, pos = _bps_read_vlv(patch_data, pos)
        meta_size, pos = _bps_read_vlv(patch_data, pos)
        pos += meta_size  # skip metadata (usually a file listing)

        if len(rom_data) != src_size:
            return {"status": "error",
                    "message": f"BPS source size mismatch (patch expects {src_size} "
                               f"bytes, ROM has {len(rom_data)} bytes)"}

        output_data = bytearray(dst_size)
        out_pos = 0
        src_rel_offset = 0
        dst_rel_offset = 0
        end = len(patch_data) - BPS_FOOTER_SIZE

        while pos < end:
            data, pos = _bps_read_vlv(patch_data, pos)
            command = data & 3
            length = (data >> 2) + 1
            if out_pos + length > dst_size:
                return {"status": "error",
                        "message": "BPS patch writes beyond declared target size"}

            if command == 0:  # TargetRead: literal bytes from the patch
                if pos + length > end:
                    return {"status": "error", "message": "BPS patch truncated in TargetRead"}
                output_data[out_pos:out_pos + length] = patch_data[pos:pos + length]
                pos += length
                out_pos += length
            elif command == 1:  # SourceRead: copy from source at output offset
                if out_pos + length > len(rom_data):
                    return {"status": "error", "message": "BPS SourceRead beyond source size"}
                output_data[out_pos:out_pos + length] = rom_data[out_pos:out_pos + length]
                out_pos += length
            elif command == 2:  # SourceCopy: relative copy from source
                offset_data, pos = _bps_read_vlv(patch_data, pos)
                magnitude = offset_data >> 1
                src_rel_offset += -magnitude if (offset_data & 1) else magnitude
                if src_rel_offset < 0 or src_rel_offset + length > len(rom_data):
                    return {"status": "error", "message": "BPS SourceCopy out of bounds"}
                output_data[out_pos:out_pos + length] = \
                    rom_data[src_rel_offset:src_rel_offset + length]
                src_rel_offset += length
                out_pos += length
            else:  # command == 3, TargetCopy: relative copy from target (may overlap)
                offset_data, pos = _bps_read_vlv(patch_data, pos)
                magnitude = offset_data >> 1
                dst_rel_offset += -magnitude if (offset_data & 1) else magnitude
                if dst_rel_offset < 0 or dst_rel_offset >= dst_size:
                    return {"status": "error", "message": "BPS TargetCopy out of bounds"}
                for _ in range(length):
                    output_data[out_pos] = output_data[dst_rel_offset]
                    out_pos += 1
                    dst_rel_offset += 1

        if out_pos != dst_size:
            return {"status": "error",
                    "message": f"BPS patch incomplete (produced {out_pos} of {dst_size} bytes)"}

        if zlib.crc32(bytes(output_data)) & 0xFFFFFFFF != target_crc:
            return {"status": "error",
                    "message": "BPS target CRC32 mismatch - output is corrupt"}

        with open(output_path, "wb") as f:
            f.write(output_data)

        return {
            "status": "patched",
            "message": f"BPS patch applied ({len(output_data)} bytes written, CRC verified)",
            "output": output_path,
        }

    except ValueError as e:
        return {"status": "error", "message": f"BPS patch error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"BPS patch error: {e}"}
