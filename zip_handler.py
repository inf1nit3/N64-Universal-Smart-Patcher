"""
zip_handler.py
Handles extraction of N64 ROMs from .zip and .7z archives.

Security-hardened: every member path is validated against path traversal
(zip-slip) before extraction and the total uncompressed size is capped,
so crafted archives cannot write outside the temp dir or fill the disk.
"""
import os
import shutil
import tempfile
import zipfile
from typing import List
import n64_core as core

# Safety cap for the total uncompressed size of one archive (1 GiB is far
# beyond any legitimate N64 ROM collection).
MAX_EXTRACT_TOTAL_BYTES = 1024 * 1024 * 1024


def is_archive(path: str) -> bool:
    """Checks if the file is a supported archive (.zip, .7z)."""
    if not os.path.isfile(path):
        return False
    return os.path.splitext(path)[1].lower() in (".zip", ".7z")


def create_extraction_dir() -> str:
    """Creates a unique temporary extraction directory (system temp area,
    never inside the user's ROM folders)."""
    return tempfile.mkdtemp(prefix="n64patch_extract_")


def _safe_member_path(temp_dir: str, member_name: str) -> str:
    """Validates an archive member name and returns its absolute target
    path. Raises ValueError for absolute paths, drive letters or any '..'
    component (zip-slip protection)."""
    normalized = member_name.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"Unsafe absolute path in archive: {member_name}")
    parts = normalized.split("/")
    if any(p == ".." for p in parts):
        raise ValueError(f"Unsafe path traversal in archive: {member_name}")
    target = os.path.realpath(os.path.join(temp_dir, *parts))
    base = os.path.realpath(temp_dir)
    if not (target == base or target.startswith(base + os.sep)):
        raise ValueError(f"Path escapes extraction directory: {member_name}")
    return target


def _extract_zip(archive_path: str, temp_dir: str) -> List[str]:
    extracted = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        rom_members = [m for m in zf.infolist()
                       if not m.is_dir() and core.is_rom_file(m.filename)]
        total = sum(m.file_size for m in rom_members)
        if total > MAX_EXTRACT_TOTAL_BYTES:
            raise RuntimeError(
                f"Archive too large ({total} bytes uncompressed) - aborted for safety")
        for member in rom_members:
            target = _safe_member_path(temp_dir, member.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            zf.extract(member, path=temp_dir)
            extracted.append(target)
    return extracted


def _extract_7z(archive_path: str, temp_dir: str) -> List[str]:
    try:
        import py7zr
    except ImportError:
        raise RuntimeError("py7zr not installed - cannot extract .7z archives "
                           "(pip install py7zr)")
    extracted = []
    with py7zr.SevenZipFile(archive_path, mode="r") as sz:
        infos = sz.list()
        rom_names = [i.filename for i in infos
                     if not i.is_directory and core.is_rom_file(i.filename)]
        if not rom_names:
            return extracted
        total = sum(getattr(i, "uncompressed", 0) or 0 for i in infos
                    if i.filename in rom_names)
        if total > MAX_EXTRACT_TOTAL_BYTES:
            raise RuntimeError(
                f"Archive too large ({total} bytes uncompressed) - aborted for safety")
        # Validate EVERY member path before extracting anything (a single
        # malicious entry aborts the whole archive), then extract only the
        # ROM members we actually need.
        for info in infos:
            _safe_member_path(temp_dir, info.filename)
        sz.extract(path=temp_dir, targets=rom_names)
        for name in rom_names:
            extracted.append(os.path.join(temp_dir, *name.replace("\\", "/").split("/")))
    return extracted


def extract_roms_from_archive(archive_path: str, temp_dir: str) -> List[str]:
    """
    Extracts N64 ROM files (.z64, .v64, .n64) from a .zip or .7z archive
    into temp_dir. Returns a list of full paths to extracted ROM files.
    Raises RuntimeError on corrupt/malicious archives (callers log it).
    """
    if not os.path.isfile(archive_path):
        raise RuntimeError(f"Archive not found: {archive_path}")

    os.makedirs(temp_dir, exist_ok=True)
    ext = os.path.splitext(archive_path)[1].lower()

    try:
        if ext == ".zip":
            return _extract_zip(archive_path, temp_dir)
        elif ext == ".7z":
            return _extract_7z(archive_path, temp_dir)
    except (ValueError, RuntimeError) as e:
        # Security / policy errors: propagate with context
        raise RuntimeError(f"{os.path.basename(archive_path)}: {e}")
    except Exception as e:
        raise RuntimeError(f"Error extracting archive {os.path.basename(archive_path)}: {e}")

    return []


def cleanup_temp_dir(temp_dir: str) -> None:
    """Removes a temporary extraction directory."""
    if temp_dir and os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)