"""
header_utils.py
Entfernt Scene-Intro-Header (iN0000, PARADOX, etc.) und repariert CRC-Checksums.
Essenziell für Flashcart-Kompatibilität und korrektes xdelta-Patching.
"""
import shutil
import subprocess
import sys

import n64_core as core

# Standard N64 ROM Magic Words
MAGIC_Z64 = bytes.fromhex("80371240")  # Big-Endian (Native)
MAGIC_V64 = bytes.fromhex("37804012")  # Byte-Swapped
MAGIC_N64 = bytes.fromhex("40123780")  # Little-Endian
ALL_MAGICS = (MAGIC_Z64, MAGIC_V64, MAGIC_N64)

# Bekannte Scene-Header-Größen
HEADER_SIZES = [512, 1024, 768, 640, 896]  # Typische Größen in Bytes

_CHUNK = 1024 * 1024


def detect_scene_header(input_path: str) -> int:
    """
    Gibt die Größe des Scene-Headers zurück (0 wenn keiner vorhanden).
    """
    with open(input_path, 'rb') as f:
        header_data = f.read(2048)

    if header_data[:4] in ALL_MAGICS:
        return 0

    for header_size in HEADER_SIZES:
        if len(header_data) > header_size + 4:
            if header_data[header_size:header_size + 4] in ALL_MAGICS:
                return header_size

    return 0


def detect_format_magic(input_path: str):
    """Liest die ersten 4 Bytes und liefert den core-Formatkey oder None."""
    try:
        with open(input_path, 'rb') as f:
            head = f.read(4)
    except OSError:
        return None
    fmt, _label = core.detect_format(head)
    return fmt


def detect_and_strip_scene_header(input_path: str, output_path: str) -> dict:
    """
    Erkennt und entfernt Scene-Release-Header (iN0000, PARADOX, etc.)
    Schreibt nur dann eine Ausgabedatei, wenn tatsächlich ein Header
    entfernt wurde - ansonsten bleibt das Original unverändert und der
    Aufrufer verwendet den Originalpfad weiter (keine Daten-Müllkopien).
    Returns: {"stripped": bool, "header_size": int, "message": str}
    """
    header_size = detect_scene_header(input_path)

    if header_size == 0:
        if detect_format_magic(input_path) is None:
            return {"stripped": False, "header_size": 0,
                    "message": "Unbekanntes ROM-Format"}
        return {"stripped": False, "header_size": 0,
                "message": "Kein Scene-Header erkannt"}

    # Header gefunden: ROM ohne den Header in Chunks nach output_path schreiben
    with open(input_path, 'rb') as src, open(output_path, 'wb') as dst:
        src.seek(header_size)
        shutil.copyfileobj(src, dst, _CHUNK)

    return {
        "stripped": True,
        "header_size": header_size,
        "message": f"Scene-Header ({header_size} Bytes) entfernt"
    }


def fix_rom_crc(rom_path: str, rn64crc_path: str = None) -> dict:
    """
    Repariert die CRC1/CRC2 Checksummen im ROM-Header.
    Essenziell für EverDrive / ED64 Flashcarts.
    Nutzt das rn64crc-Tool wenn ausführbar, sonst die eingebaute
    Pure-Python-Engine (funktioniert auf allen Plattformen).
    Returns: {"status": "fixed"|"error", "message": str}
    """
    tool = rn64crc_path or core.RN64CRC_PATH
    if core._is_runnable(tool):
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

            if result.returncode == 0:
                return {"status": "fixed", "message": "CRC1/CRC2 repariert (rn64crc)"}
            # Tool meldete Fehler -> native Engine versuchen
        except (subprocess.TimeoutExpired, OSError):
            pass  # native Engine versuchen

    ok, msg = core.fix_rom_crc_native(rom_path)
    if ok:
        return {"status": "fixed", "message": msg}
    return {"status": "error", "message": f"CRC-Fix fehlgeschlagen: {msg}"}


def get_rom_info_from_header(rom_path: str) -> dict:
    """
    Liest wichtige Infos direkt aus dem ROM-Header.
    Layout (big-endian z64): 0x20 Titel, 0x3B-0x3D Cartridge-ID,
    0x3E Ländercode, 0x3F Version (entspricht libdragon REGION_OFFSET
    und n64_core).
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
    info["game_code"] = header[0x3B:0x3E].decode('ascii', errors='ignore').strip()
    info["crc1"] = header[0x10:0x14].hex().upper()
    info["crc2"] = header[0x14:0x18].hex().upper()
    info["version"] = header[0x3F]

    country_byte = header[0x3E]
    country_code = chr(country_byte) if 32 <= country_byte < 127 else "?"
    info["region"] = core.REGION_MAP.get(country_code, f"Unknown ({country_code})")

    return info