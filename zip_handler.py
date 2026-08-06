"""
zip_handler.py
Handles extraction of N64 ROMs from .zip and .7z archives.
"""
import os
import shutil
import zipfile
from typing import List
import n64_core as core


def is_archive(path: str) -> bool:
    """Checks if the file extension is a supported archive (.zip, .7z)."""
    ext = os.path.splitext(path)[1].lower()
    return ext in (".zip", ".7z")


def extract_roms_from_archive(archive_path: str, temp_dir: str) -> List[str]:
    """
    Extracts N64 ROM files (.z64, .v64, .n64) from a .zip or .7z archive into temp_dir.
    Returns a list of full paths to extracted ROM files.
    """
    extracted_roms = []
    if not os.path.exists(archive_path):
        return extracted_roms

    os.makedirs(temp_dir, exist_ok=True)
    ext = os.path.splitext(archive_path)[1].lower()

    if ext == ".zip":
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.infolist():
                    if core.is_rom_file(member.filename):
                        target_path = zf.extract(member, path=temp_dir)
                        extracted_roms.append(target_path)
        except Exception as e:
            print(f"Error extracting ZIP archive {archive_path}: {e}")

    elif ext == ".7z":
        try:
            import py7zr
            with py7zr.SevenZipFile(archive_path, mode="r") as sz:
                all_files = sz.getnames()
                rom_files = [f for f in all_files if core.is_rom_file(f)]
                if rom_files:
                    sz.extract(path=temp_dir, targets=rom_files)
                    for f in rom_files:
                        extracted_roms.append(os.path.join(temp_dir, f))
        except ImportError:
            print(f"py7zr not installed; cannot extract 7z archive {archive_path}")
        except Exception as e:
            print(f"Error extracting 7z archive {archive_path}: {e}")

    return extracted_roms


def cleanup_temp_dir(temp_dir: str) -> None:
    """Removes temporary extraction directory."""
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            print(f"Error cleaning up temp directory {temp_dir}: {e}")
