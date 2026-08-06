"""
header_utils.py
Entfernt Scene-Intro-Header (iN0000, PARADOX, etc.) und repariert CRC-Checksums.
Essenziell für Flashcart-Kompatibilität und korrektes xdelta-Patching.
"""
import struct
import os
import subprocess
import sys

# Standard N64 ROM Magic Words
MAGIC_Z64 = bytes.fromhex("80371240")  # Big-Endian (Native)
MAGIC_V64 = bytes.fromhex("37804012")  # Byte-Swapped
MAGIC_N64 = bytes.fromhex("40123780")  # Little-Endian

# Bekannte Scene-Header-Größen
HEADER_SIZES = [512, 1024, 768, 640, 896]  # Typische Größen in Bytes


def detect_and_strip_scene_header(input_path: str, output_path: str) -> dict:
    """
    Erkennt und entfernt Scene-Release-Header (iN0000, PARADOX, etc.)
    Returns: {"stripped": bool, "header_size": int, "message": str}
    """
    with open(input_path, 'rb') as f:
        header_data = f.read(2048)  # Mehr als genug für jeden Header
    
    # Prüfe ob Magic Word bei Standard-Position (0x00) vorhanden ist
    if header_data[:4] in [MAGIC_Z64, MAGIC_V64, MAGIC_N64]:
        # Kein Scene-Header vorhanden
        if input_path != output_path:
            with open(input_path, 'rb') as src, open(output_path, 'wb') as dst:
                dst.write(src.read())
        return {"stripped": False, "header_size": 0, "message": "Kein Scene-Header erkannt"}
    
    # Suche nach Magic Word in typischen Header-Offsets
    for header_size in HEADER_SIZES:
        if len(header_data) > header_size + 4:
            potential_magic = header_data[header_size:header_size + 4]
            if potential_magic in [MAGIC_Z64, MAGIC_V64, MAGIC_N64]:
                # Header gefunden! Jetzt das ROM ohne Header schreiben
                with open(input_path, 'rb') as f:
                    f.seek(header_size)
                    rom_data = f.read()
                
                with open(output_path, 'wb') as f:
                    f.write(rom_data)
                
                return {
                    "stripped": True,
                    "header_size": header_size,
                    "message": f"Scene-Header ({header_size} Bytes) entfernt"
                }
    
    # Kein Header gefunden - kopiere Original
    if input_path != output_path:
        with open(input_path, 'rb') as src, open(output_path, 'wb') as dst:
            dst.write(src.read())
    return {"stripped": False, "header_size": 0, "message": "Unbekanntes ROM-Format"}


def detect_scene_header(input_path: str) -> int:
    """
    Gibt die Größe des Scene-Headers zurück (0 wenn keiner vorhanden).
    """
    with open(input_path, 'rb') as f:
        header_data = f.read(2048)
    
    if header_data[:4] in [MAGIC_Z64, MAGIC_V64, MAGIC_N64]:
        return 0
    
    for header_size in HEADER_SIZES:
        if len(header_data) > header_size + 4:
            potential_magic = header_data[header_size:header_size + 4]
            if potential_magic in [MAGIC_Z64, MAGIC_V64, MAGIC_N64]:
                return header_size
    
    return 0


def fix_rom_crc(rom_path: str, rn64crc_path: str) -> dict:
    """
    Repariert die CRC1/CRC2 Checksummen im ROM-Header.
    Essenziell für EverDrive / ED64 Flashcarts.
    Returns: {"status": "fixed"|"error"|"skipped", "message": str}
    """
    if not os.path.isfile(rn64crc_path):
        return {"status": "skipped", "message": "rn64crc.exe nicht gefunden"}
    
    try:
        CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
        result = subprocess.run(
            [rn64crc_path, "-u", rom_path],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=30
        )
        
        if result.returncode == 0:
            return {"status": "fixed", "message": "CRC1/CRC2 repariert"}
        else:
            return {"status": "error", "message": f"CRC-Fix fehlgeschlagen: {result.stderr}"}
    
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "CRC-Fix Timeout"}
    except Exception as e:
        return {"status": "error", "message": f"CRC-Fix Fehler: {str(e)}"}


def get_rom_info_from_header(rom_path: str) -> dict:
    """
    Liest wichtige Infos direkt aus dem ROM-Header.
    """
    info = {
        "title": "",
        "game_code": "",
        "region": "",
        "crc1": "",
        "crc2": "",
        "version": 0
    }
    
    with open(rom_path, 'rb') as f:
        # Überspringe ggf. Scene-Header
        header_size = detect_scene_header(rom_path)
        f.seek(header_size)
        header = f.read(64)
    
    if len(header) < 64:
        return info
    
    info["title"] = header[0x20:0x34].decode('ascii', errors='ignore').strip()
    info["game_code"] = header[0x3B:0x3F].decode('ascii', errors='ignore').strip()
    info["crc1"] = header[0x10:0x14].hex().upper()
    info["crc2"] = header[0x14:0x18].hex().upper()
    info["version"] = header[0x3E]
    
    country_byte = header[0x3F]
    region_map = {
        ord('A'): "All Regions",
        ord('E'): "USA",
        ord('J'): "Japan",
        ord('P'): "Europe",
        ord('D'): "Germany",
        ord('F'): "France",
        ord('I'): "Italy",
        ord('S'): "Spain",
        ord('U'): "Australia",
        ord('X'): "PAL",
    }
    info["region"] = region_map.get(country_byte, f"Unknown ({chr(country_byte)})")
    
    return info
