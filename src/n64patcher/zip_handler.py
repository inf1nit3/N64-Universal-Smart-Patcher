"""
zip_handler.py
Handles extraction of N64 ROMs from .zip and .7z archives.

Security-hardened: every member path is validated against path traversal
(zip-slip) before extraction and the total uncompressed size is capped,
so crafted archives cannot write outside the temp dir or fill the disk.
"""
import os
import shutil
import stat
import tempfile
import zipfile

from . import n64_core as core

# Safety cap for the total uncompressed size of one archive (1 GiB is far
# beyond any legitimate N64 ROM collection). Enforced against bytes that
# actually arrive, never against the size the archive declares.
MAX_EXTRACT_TOTAL_BYTES = 1024 * 1024 * 1024

# A ROM compresses maybe 3-5x. Two orders of magnitude beyond that is not
# a ROM, it is a decompression bomb.
MAX_COMPRESSION_RATIO = 200

_COPY_CHUNK = 1024 * 1024


def _is_zip_symlink(member: zipfile.ZipInfo) -> bool:
    """Unix mode lives in the high 16 bits of external_attr. A symlink
    member would otherwise be materialized as a link pointing anywhere."""
    return stat.S_ISLNK(member.external_attr >> 16)


def _is_7z_symlink(info: object) -> bool:
    """py7zr exposes the unix mode in the high 16 bits of `attributes`,
    flagged by FILE_ATTRIBUTE_UNIX_EXTENSION (0x8000); Windows-style links
    show up as reparse points (0x400)."""
    if getattr(info, "is_symlink", False):
        return True
    attrs = getattr(info, "attributes", 0) or 0
    if attrs & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
        return True
    return bool(attrs & 0x8000) and stat.S_ISLNK(attrs >> 16)


def _extract_member(zf: zipfile.ZipFile, member: zipfile.ZipInfo,
                    target: str, budget: list[int]) -> None:
    """Stream one member to *target*, counting real bytes against *budget*.

    zf.extract() is deliberately not used: it applies its own sanitization
    and picks its own destination, which can differ from the path we
    validated, so what gets checked would not be what gets written.
    """
    written = 0
    with zf.open(member, "r") as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(_COPY_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise RuntimeError(
                    f"Archive exceeds the {MAX_EXTRACT_TOTAL_BYTES} byte "
                    f"extraction cap - aborted for safety")
            if (member.compress_size > 0
                    and written > member.compress_size * MAX_COMPRESSION_RATIO):
                raise RuntimeError(
                    f"'{member.filename}' expands more than "
                    f"{MAX_COMPRESSION_RATIO}x - refusing to continue")
            dst.write(chunk)


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


def _extract_zip(archive_path: str, temp_dir: str) -> list[str]:
    extracted = []
    budget = [MAX_EXTRACT_TOTAL_BYTES]
    with zipfile.ZipFile(archive_path, "r") as zf:
        rom_members = [m for m in zf.infolist()
                       if not m.is_dir() and core.is_rom_file(m.filename)]
        for member in rom_members:
            if _is_zip_symlink(member):
                raise ValueError(
                    f"Refusing to extract symlink member: {member.filename}")
        # The declared file_size is attacker-controlled metadata, so it is
        # only used to reject the obviously absurd up front. The cap that
        # matters is enforced byte-by-byte in _extract_member.
        declared = sum(m.file_size for m in rom_members)
        if declared > MAX_EXTRACT_TOTAL_BYTES:
            raise RuntimeError(
                f"Archive declares {declared} bytes uncompressed - aborted for safety")
        for member in rom_members:
            target = _safe_member_path(temp_dir, member.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            _extract_member(zf, member, target, budget)
            extracted.append(target)
    return extracted


def _extract_7z(archive_path: str, temp_dir: str) -> list[str]:
    try:
        import py7zr
    except ImportError as e:
        raise RuntimeError("py7zr not installed - cannot extract .7z archives "
                           "(pip install 'n64patcher[archive]')") from e
    extracted: list[str] = []
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
                f"Archive declares {total} bytes uncompressed - aborted for safety")
        # Validate EVERY member path before extracting anything (a single
        # malicious entry aborts the whole archive), then extract only the
        # ROM members we actually need.
        for info in infos:
            _safe_member_path(temp_dir, info.filename)
            if _is_7z_symlink(info):
                raise ValueError(
                    f"Refusing to extract symlink member: {info.filename}")
        sz.extract(path=temp_dir, targets=rom_names)
        for name in rom_names:
            extracted.append(os.path.join(temp_dir, *name.replace("\\", "/").split("/")))
    # py7zr does its own writing, so verify afterwards rather than trust it:
    # every result must be a regular file that really landed inside temp_dir.
    _verify_extracted(temp_dir, extracted)
    return extracted


def _verify_extracted(temp_dir: str, paths: list[str]) -> None:
    """Backstop for extractors that choose their own destination: confirm
    each path is a regular file resolving inside temp_dir. Anything else is
    removed and the archive rejected."""
    base = os.path.realpath(temp_dir)
    for path in paths:
        if os.path.islink(path):
            os.unlink(path)
            raise ValueError(f"Archive produced a symlink: {path}")
        if not os.path.isfile(path):
            raise ValueError(f"Archive member did not extract as a file: {path}")
        resolved = os.path.realpath(path)
        if not resolved.startswith(base + os.sep):
            raise ValueError(f"Archive member escaped the extraction dir: {path}")


def extract_roms_from_archive(archive_path: str, temp_dir: str) -> list[str]:
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
        raise RuntimeError(f"{os.path.basename(archive_path)}: {e}") from e
    except Exception as e:
        raise RuntimeError(
            f"Error extracting archive {os.path.basename(archive_path)}: {e}") from e

    return []


def cleanup_temp_dir(temp_dir: str) -> None:
    """Removes a temporary extraction directory."""
    if temp_dir and os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
