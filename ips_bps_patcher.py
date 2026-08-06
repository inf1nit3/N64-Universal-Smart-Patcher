"""
ips_bps_patcher.py
Applies IPS and BPS community patch files to N64 ROMs.
"""
import os
import struct
from typing import Dict, Any


def detect_patch_type(patch_path: str) -> str:
    """Detects patch format based on extension and magic bytes."""
    ext = os.path.splitext(patch_path)[1].lower().lstrip(".")
    if ext in ("ips", "bps", "ups"):
        return ext

    if os.path.isfile(patch_path):
        with open(patch_path, "rb") as f:
            header = f.read(4)
        if header.startswith(b"PATCH"):
            return "ips"
        elif header.startswith(b"BPS1"):
            return "bps"
        elif header.startswith(b"UPS1"):
            return "ups"

    return "unknown"


def apply_ips_patch(rom_path: str, patch_path: str, output_path: str) -> Dict[str, Any]:
    """Applies a standard IPS patch to the ROM file."""
    if not os.path.isfile(rom_path) or not os.path.isfile(patch_path):
        return {"status": "error", "message": "Source ROM or patch file missing"}

    try:
        with open(rom_path, "rb") as f:
            rom_data = bytearray(f.read())

        with open(patch_path, "rb") as f:
            patch_data = f.read()

        if not patch_data.startswith(b"PATCH"):
            return {"status": "error", "message": "Invalid IPS patch header"}

        pos = 5
        records_applied = 0

        while pos < len(patch_data):
            if patch_data[pos:pos+3] == b"EOF":
                break

            offset = (patch_data[pos] << 16) | (patch_data[pos+1] << 8) | patch_data[pos+2]
            size = (patch_data[pos+3] << 8) | patch_data[pos+4]
            pos += 5

            if size > 0:
                # Normal record
                val_bytes = patch_data[pos:pos+size]
                pos += size
            else:
                # RLE record
                rle_size = (patch_data[pos] << 8) | patch_data[pos+1]
                val_byte = patch_data[pos+2:pos+3]
                val_bytes = val_byte * rle_size
                pos += 3

            # Expand rom_data array if needed
            required_len = offset + len(val_bytes)
            if len(rom_data) < required_len:
                rom_data.extend(b"\x00" * (required_len - len(rom_data)))

            rom_data[offset:offset+len(val_bytes)] = val_bytes
            records_applied += 1

        with open(output_path, "wb") as f:
            f.write(rom_data)

        return {
            "status": "patched",
            "message": f"IPS patch applied ({records_applied} records)",
            "output": output_path,
        }

    except Exception as e:
        return {"status": "error", "message": f"IPS patch error: {e}"}


def apply_bps_patch(rom_path: str, patch_path: str, output_path: str) -> Dict[str, Any]:
    """Applies a BPS patch (basic implementation)."""
    if not os.path.isfile(rom_path) or not os.path.isfile(patch_path):
        return {"status": "error", "message": "Source ROM or patch file missing"}

    try:
        with open(rom_path, "rb") as f:
            rom_data = bytearray(f.read())

        with open(patch_path, "rb") as f:
            patch_data = f.read()

        if not patch_data.startswith(b"BPS1"):
            return {"status": "error", "message": "Invalid BPS patch header"}

        # Basic BPS decoder
        pos = 4
        def read_vlv():
            nonlocal pos
            result = 0
            shift = 0
            while True:
                b = patch_data[pos]
                pos += 1
                result |= (b & 0x7f) << shift
                if b & 0x80:
                    break
                shift += 7
                result += 1 << shift
            return result

        src_size = read_vlv()
        dst_size = read_vlv()
        meta_size = read_vlv()
        pos += meta_size

        output_data = bytearray(dst_size)
        out_pos = 0
        src_offset = 0
        dst_offset = 0

        while pos < len(patch_data) - 12:
            data = read_vlv()
            command = data & 3
            length = (data >> 2) + 1

            if command == 0:  # SourceRead
                output_data[out_pos:out_pos+length] = rom_data[out_pos:out_pos+length]
                out_pos += length
            elif command == 1:  # TargetRead
                output_data[out_pos:out_pos+length] = patch_data[pos:pos+length]
                pos += length
                out_pos += length
            elif command == 2:  # SourceCopy
                val = read_vlv()
                src_offset += -val if (val & 1) else (val >> 1)
                output_data[out_pos:out_pos+length] = rom_data[src_offset:src_offset+length]
                out_pos += length
                src_offset += length
            elif command == 3:  # TargetCopy
                val = read_vlv()
                dst_offset += -val if (val & 1) else (val >> 1)
                for _ in range(length):
                    output_data[out_pos] = output_data[dst_offset]
                    out_pos += 1
                    dst_offset += 1

        with open(output_path, "wb") as f:
            f.write(output_data)

        return {
            "status": "patched",
            "message": f"BPS patch applied ({len(output_data)} bytes written)",
            "output": output_path,
        }

    except Exception as e:
        return {"status": "error", "message": f"BPS patch error: {e}"}
