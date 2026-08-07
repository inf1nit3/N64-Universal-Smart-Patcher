"""No-Intro / Redump DAT lookup.

A DAT file is a catalogue of known-good dumps. Matching a ROM against one
answers questions the header cannot: is this an unmodified dump, which
revision is it, and what is the dump actually called.

DAT files are **not bundled**. They are large, revised constantly, and
their redistribution terms are unclear, so shipping a stale copy would be
both wrong and quickly wrong. Supply your own:

  1. ``--dat path/to/file.dat``
  2. ``~/.n64patcher/dats/*.dat`` (or ``.xml``), picked up automatically

Format is Logiqx XML, which is what No-Intro and Redump both emit::

    <datafile>
      <header><name>Nintendo - Nintendo 64</name><version>...</version></header>
      <game name="Super Mario 64 (USA)">
        <rom name="..." size="8388608" crc="4EAA3D0E" md5="..." sha1="..."/>
      </game>
    </datafile>

Note the keys are hashes of the *file*, unrelated to the N64 boot checksums
at header 0x10/0x14 that the patch database matches on. A ROM can be a
verified dump and still have no patch recipe, and vice versa.

Parsing is cached: a full N64 DAT is a few thousand entries, and re-parsing
the XML for every ROM in a 1500-file batch would dominate the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
import zlib
from typing import Any

USER_DAT_DIR = os.path.join(os.path.expanduser("~"), ".n64patcher", "dats")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".n64patcher", "dat-cache")
ENV_DAT = "N64PATCHER_DAT"

DAT_EXTENSIONS = (".dat", ".xml")
CACHE_VERSION = 1


class DatError(ValueError):
    """The DAT file could not be read as Logiqx XML."""


def dat_paths(explicit: list[str] | None = None) -> list[str]:
    """DAT files to load: explicit ones first, then the user directory."""
    paths: list[str] = list(explicit or [])
    env = os.environ.get(ENV_DAT)
    if env:
        paths.extend(p for p in env.split(os.pathsep) if p)
    if os.path.isdir(USER_DAT_DIR):
        paths.extend(
            os.path.join(USER_DAT_DIR, n)
            for n in sorted(os.listdir(USER_DAT_DIR))
            if n.lower().endswith(DAT_EXTENSIONS)
        )
    seen: set[str] = set()
    unique = []
    for p in paths:
        key = os.path.abspath(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _cache_path(dat_path: str) -> str:
    st = os.stat(dat_path)
    # Keyed on identity + mtime + size: editing or replacing the DAT
    # invalidates the cache without needing to re-hash the whole file.
    token = f"{os.path.abspath(dat_path)}|{st.st_mtime_ns}|{st.st_size}|{CACHE_VERSION}"
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{digest}.json")


def parse_dat(path: str) -> dict[str, Any]:
    """Parse a Logiqx DAT into {"name", "version", "entries": [...]}."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as e:
        raise DatError(f"{os.path.basename(path)}: {e}") from e

    root = tree.getroot()
    if root.tag != "datafile":
        raise DatError(
            f"{os.path.basename(path)}: root element is <{root.tag}>, "
            f"expected <datafile> (Logiqx XML)")

    header = root.find("header")
    dat_name = ""
    dat_version = ""
    if header is not None:
        dat_name = (header.findtext("name") or "").strip()
        dat_version = (header.findtext("version") or "").strip()

    entries = []
    for game in root.iter("game"):
        game_name = (game.get("name") or "").strip()
        for rom in game.findall("rom"):
            crc = (rom.get("crc") or "").strip().upper()
            md5 = (rom.get("md5") or "").strip().upper()
            sha1 = (rom.get("sha1") or "").strip().upper()
            if not (crc or md5 or sha1):
                continue
            try:
                size = int(rom.get("size") or 0)
            except ValueError:
                size = 0
            entries.append({
                "game": game_name,
                "rom": (rom.get("name") or "").strip(),
                "size": size,
                "crc32": crc,
                "md5": md5,
                "sha1": sha1,
            })
    if not entries:
        raise DatError(f"{os.path.basename(path)}: no <rom> entries found")
    return {"name": dat_name, "version": dat_version, "entries": entries}


def _load_cached(path: str) -> dict[str, Any] | None:
    cache = _cache_path(path)
    if not os.path.isfile(cache):
        return None
    try:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _store_cache(path: str, data: dict[str, Any]) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(path), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass  # a warm cache is an optimization, never a requirement


class DatIndex:
    """Hash -> dump entry, merged across every loaded DAT."""

    def __init__(self) -> None:
        self.by_crc32: dict[str, dict[str, Any]] = {}
        self.by_md5: dict[str, dict[str, Any]] = {}
        self.by_sha1: dict[str, dict[str, Any]] = {}
        self.sources: list[str] = []

    def __len__(self) -> int:
        # sha1 is the most reliably populated key across DAT producers.
        return len(self.by_sha1) or len(self.by_md5) or len(self.by_crc32)

    def __bool__(self) -> bool:
        return bool(self.by_crc32 or self.by_md5 or self.by_sha1)

    def add(self, parsed: dict[str, Any]) -> None:
        label = parsed.get("name") or "DAT"
        if parsed.get("version"):
            label = f"{label} ({parsed['version']})"
        self.sources.append(label)
        for entry in parsed["entries"]:
            if entry["crc32"]:
                self.by_crc32.setdefault(entry["crc32"], entry)
            if entry["md5"]:
                self.by_md5.setdefault(entry["md5"], entry)
            if entry["sha1"]:
                self.by_sha1.setdefault(entry["sha1"], entry)

    def lookup(self, crc32: str = "", md5: str = "",
               sha1: str = "") -> dict[str, Any] | None:
        """Strongest available hash first: sha1, then md5, then crc32."""
        for value, table in ((sha1, self.by_sha1), (md5, self.by_md5),
                             (crc32, self.by_crc32)):
            if value:
                hit = table.get(value.upper())
                if hit is not None:
                    return hit
        return None


def load_dats(paths: list[str] | None = None, on_error: Any = None) -> DatIndex:
    """Load and index DAT files, using the parse cache where valid."""
    def report(msg: str) -> None:
        if on_error is not None:
            on_error(msg)

    index = DatIndex()
    for path in dat_paths(paths):
        if not os.path.isfile(path):
            report(f"dat: not found: {path}")
            continue
        parsed = _load_cached(path)
        if parsed is None:
            try:
                parsed = parse_dat(path)
            except DatError as e:
                report(f"dat: {e}")
                continue
            _store_cache(path, parsed)
        index.add(parsed)
    return index


def file_hashes(path: str, chunk_size: int = 1024 * 1024) -> dict[str, str]:
    """CRC32, MD5 and SHA-1 of a file in a single read pass."""
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    crc = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return {
        "crc32": f"{crc & 0xFFFFFFFF:08X}",
        "md5": md5.hexdigest().upper(),
        "sha1": sha1.hexdigest().upper(),
    }


def describe(index: DatIndex) -> str:
    if not index:
        return ("No DAT files loaded.\n"
                f"Put a No-Intro/Redump .dat in {USER_DAT_DIR} "
                f"or pass --dat <file>.")
    lines = [f"{len(index)} dump(s) indexed from {len(index.sources)} DAT file(s):"]
    lines.extend(f"  {s}" for s in index.sources)
    return "\n".join(lines)
