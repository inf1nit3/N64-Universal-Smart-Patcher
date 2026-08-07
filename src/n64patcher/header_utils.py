"""
n64patcher.header_utils - scene header stripping and CRC repair.

Strips scene intro headers (iN0000, PARADOX, ...) and repairs the boot
checksums. Both matter for flashcart compatibility and for xdelta patches
to line up, since a delta built against a headerless dump will not apply
to one carrying a 512-byte intro.
"""
import shutil
import subprocess
import sys

from . import n64_core as core

# Standard N64 ROM Magic Words
MAGIC_Z64 = bytes.fromhex("80371240")  # Big-Endian (Native)
MAGIC_V64 = bytes.fromhex("37804012")  # Byte-Swapped
MAGIC_N64 = bytes.fromhex("40123780")  # Little-Endian
ALL_MAGICS = (MAGIC_Z64, MAGIC_V64, MAGIC_N64)

# Known scene header sizes, in bytes
HEADER_SIZES = [512, 1024, 768, 640, 896]

_CHUNK = 1024 * 1024


def detect_scene_header(input_path: str) -> int:
    """Size of the scene header in bytes, or 0 when there is none."""
    with open(input_path, 'rb') as f:
        header_data = f.read(2048)

    if header_data[:4] in ALL_MAGICS:
        return 0

    for header_size in HEADER_SIZES:
        if (len(header_data) > header_size + 4
                and header_data[header_size:header_size + 4] in ALL_MAGICS):
            return header_size

    return 0


def detect_format_magic(input_path: str):
    """Read the first 4 bytes and return the core format key, or None."""
    try:
        with open(input_path, 'rb') as f:
            head = f.read(4)
    except OSError:
        return None
    fmt, _label = core.detect_format(head)
    return fmt


def detect_and_strip_scene_header(input_path: str, output_path: str) -> dict:
    """
    Detect and remove scene release headers (iN0000, PARADOX, ...).

    An output file is written only when a header was actually removed;
    otherwise the original is left untouched and the caller keeps using
    the original path, so no pointless full-size copies pile up.
    Returns: {"stripped": bool, "header_size": int, "message": str}
    """
    header_size = detect_scene_header(input_path)

    if header_size == 0:
        if detect_format_magic(input_path) is None:
            return {"stripped": False, "header_size": 0,
                    "message": "Unknown ROM format"}
        return {"stripped": False, "header_size": 0,
                "message": "No scene header detected"}

    # Header found: copy the ROM without it to output_path, in chunks
    with open(input_path, 'rb') as src, open(output_path, 'wb') as dst:
        src.seek(header_size)
        shutil.copyfileobj(src, dst, _CHUNK)

    return {
        "stripped": True,
        "header_size": header_size,
        "message": f"Scene header ({header_size} bytes) removed"
    }


def fix_rom_crc(rom_path: str, rn64crc_path: str | None = None) -> dict:
    """
    Repair the CRC1/CRC2 checksums in the ROM header, which EverDrive /
    ED64 flashcarts require. Uses the rn64crc tool when it is runnable
    and actually produced valid checksums, otherwise the built-in
    pure-Python engine (which works on every platform).
    Returns: {"status": "fixed"|"error", "message": str}
    """
    tool = rn64crc_path or core.RN64CRC_PATH
    # rn64crc expects a native big-endian image; byte-swapped .v64/.n64
    # files go straight to the native engine, which preserves their order.
    if core._is_runnable(tool) and detect_format_magic(rom_path) == "z64":
        try:
            CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
            result = subprocess.run(
                [tool, "-u", rom_path],
                capture_output=True,
                text=True,
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=30
            )

            # rn64crc exits 0 even when it cannot identify the boot chip and
            # leaves the header untouched, so the file has to be re-read to
            # tell a real repair from a silent no-op. Believing the exit code
            # here shipped ROMs that black-screen on the very flashcarts this
            # function exists to serve.
            if result.returncode == 0 and core.crc_header_is_valid(rom_path):
                return {"status": "fixed", "message": "CRC1/CRC2 repaired (rn64crc)"}
            # Tool failed, or left invalid checksums -> use the native engine
        except (subprocess.TimeoutExpired, OSError):
            pass  # fall through to the native engine

    ok, msg = core.fix_rom_crc_native(rom_path)
    if ok:
        return {"status": "fixed", "message": msg}
    return {"status": "error", "message": f"CRC fix failed: {msg}"}


def get_rom_info_from_header(rom_path: str) -> dict:
    """
    Read the key fields straight out of the ROM header.
    Layout (big-endian z64): 0x20 title, 0x3B media type, 0x3C-0x3D
    cartridge ID, 0x3E country code, 0x3F version.

    game_code spans 0x3B-0x3E (four characters, e.g. "NSME"), matching
    n64_core.inspect_rom_details["game_id"]. This used to read only three
    characters here, so the CSV export and the GUI reported different
    values for the same ROM.
    """
    info = {
        "title": "",
        "game_code": "",
        "region": "",
        "crc1": "",
        "crc2": "",
        "version": 0
    }

    header_size = detect_scene_header(rom_path)
    with open(rom_path, 'rb') as f:
        f.seek(header_size)
        header = f.read(64)

    if len(header) < 64:
        return info

    info["title"] = header[0x20:0x34].decode('ascii', errors='ignore').strip()
    info["game_code"] = header[0x3B:0x3F].decode('ascii', errors='ignore').strip()
    info["crc1"] = header[0x10:0x14].hex().upper()
    info["crc2"] = header[0x14:0x18].hex().upper()
    info["version"] = header[0x3F]

    country_byte = header[0x3E]
    country_code = chr(country_byte) if 32 <= country_byte < 127 else "?"
    info["region"] = core.REGION_MAP.get(country_code, f"Unknown ({country_code})")

    return info
