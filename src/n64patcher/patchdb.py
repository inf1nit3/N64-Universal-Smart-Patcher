"""Declarative patch database.

Patch recipes live in data files rather than in Python, so the set of
supported dumps can grow without a code change or a release. Each entry is
keyed on the CRC1/CRC2 of the exact ROM it was built against, because a
delta only applies to that dump.

Search order (later files win on a key collision, so a user entry can
override a bundled one):

  1. ``<package>/patches/*.json``      - shipped with the tool
  2. ``~/.n64patcher/patches/*.json``  - per-user, survives reinstalls
  3. ``$N64PATCHER_PATCHES/*.json``    - explicit override for testing/CI

JSON is the canonical format because it needs no third-party parser - the
engine installs with zero dependencies and that is worth keeping. ``.yaml``
files are also read when PyYAML happens to be installed, but nothing here
requires it.

A malformed entry is skipped with a reason rather than taking the whole
database down: a bad community file must not stop the tool from patching
the dumps it already understands.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

SCHEMA_VERSION = 1

#: Operations a recipe may ask for. Unknown types make an entry invalid
#: rather than being ignored, so a recipe never applies *partially*.
KNOWN_OPERATIONS = ("xdelta", "poke")

#: Capabilities an entry can advertise; consumed by hires_support and the UI.
KNOWN_CAPABILITIES = ("hires", "noaa", "nodither", "widescreen", "misc")

USER_PATCH_DIR = os.path.join(os.path.expanduser("~"), ".n64patcher", "patches")
ENV_PATCH_DIR = "N64PATCHER_PATCHES"


class PatchDBError(ValueError):
    """A recipe is malformed. Carries the entry id when one is known."""


def _bundled_patch_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "patches")


def patch_dirs() -> list[str]:
    """Directories searched for recipe files, lowest precedence first."""
    dirs = [_bundled_patch_dir(), USER_PATCH_DIR]
    env = os.environ.get(ENV_PATCH_DIR)
    if env:
        dirs.extend(p for p in env.split(os.pathsep) if p)
    return dirs


def _load_file(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        if path.lower().endswith((".yaml", ".yml")):
            try:
                import yaml  # optional: never required, JSON is canonical
            except ImportError as e:
                raise PatchDBError(
                    f"{os.path.basename(path)}: YAML recipes need PyYAML "
                    f"(pip install pyyaml), or convert the file to .json"
                ) from e
            data = yaml.safe_load(f)
        else:
            data = json.load(f)
    if not isinstance(data, dict):
        raise PatchDBError(f"{os.path.basename(path)}: top level must be an object")
    return data


def _parse_crc(value: Any, field: str, entry_id: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16)
        except ValueError:
            pass
    raise PatchDBError(f"{entry_id}: {field} must be a hex string or int, got {value!r}")


def validate_entry(entry: Any, source: str = "<memory>") -> dict[str, Any]:
    """Return a normalized entry, or raise PatchDBError.

    Normalizing here means every consumer sees ints for the CRCs and a
    predictable shape, instead of each call site re-parsing hex strings.
    """
    if not isinstance(entry, dict):
        raise PatchDBError(f"{source}: each patch must be an object")

    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise PatchDBError(f"{source}: every patch needs a non-empty string id")

    match = entry.get("match")
    if not isinstance(match, dict):
        raise PatchDBError(f"{entry_id}: missing 'match' object")
    if "crc1" not in match or "crc2" not in match:
        raise PatchDBError(f"{entry_id}: match needs both crc1 and crc2")
    crc1 = _parse_crc(match["crc1"], "crc1", entry_id)
    crc2 = _parse_crc(match["crc2"], "crc2", entry_id)

    ops = entry.get("operations")
    if not isinstance(ops, list) or not ops:
        raise PatchDBError(f"{entry_id}: 'operations' must be a non-empty list")
    norm_ops = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise PatchDBError(f"{entry_id}: operation {i} must be an object")
        op_type = op.get("type")
        if op_type not in KNOWN_OPERATIONS:
            # Fail the whole entry: applying some operations but not others
            # would leave a half-patched ROM, which is worse than skipping.
            raise PatchDBError(
                f"{entry_id}: operation {i} has unknown type {op_type!r} "
                f"(known: {', '.join(KNOWN_OPERATIONS)})")
        if op_type == "xdelta" and not isinstance(op.get("file"), str):
            raise PatchDBError(f"{entry_id}: xdelta operation needs a 'file'")
        if op_type == "poke":
            if not isinstance(op.get("offset"), int):
                raise PatchDBError(f"{entry_id}: poke needs an integer 'offset'")
            if not isinstance(op.get("bytes"), str):
                raise PatchDBError(f"{entry_id}: poke needs hex 'bytes'")
            try:
                bytes.fromhex(op["bytes"])
            except ValueError as e:
                raise PatchDBError(f"{entry_id}: poke 'bytes' is not valid hex") from e
        norm_ops.append(dict(op))

    provides = entry.get("provides", [])
    if not isinstance(provides, list) or not all(isinstance(p, str) for p in provides):
        raise PatchDBError(f"{entry_id}: 'provides' must be a list of strings")
    unknown = [p for p in provides if p not in KNOWN_CAPABILITIES]
    if unknown:
        raise PatchDBError(
            f"{entry_id}: unknown capability {unknown!r} "
            f"(known: {', '.join(KNOWN_CAPABILITIES)})")

    return {
        "id": entry_id,
        "name": entry.get("name") or entry_id,
        "source": entry.get("source", ""),
        "notes": entry.get("notes", ""),
        "crc1": crc1,
        "crc2": crc2,
        "provides": list(provides),
        "operations": norm_ops,
        "origin": source,
    }


def load_patch_db(dirs: list[str] | None = None,
                  on_error: Any = None) -> dict[tuple[int, int], dict[str, Any]]:
    """Load and merge every recipe file. Returns {(crc1, crc2): entry}.

    *on_error* is called with a human-readable string for each problem found;
    loading continues regardless.
    """
    def report(msg: str) -> None:
        if on_error is not None:
            on_error(msg)

    db: dict[tuple[int, int], dict[str, Any]] = {}
    for directory in (dirs if dirs is not None else patch_dirs()):
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith((".json", ".yaml", ".yml")):
                continue
            path = os.path.join(directory, name)
            try:
                data = _load_file(path)
            except (PatchDBError, OSError, ValueError) as e:
                report(f"patch db: {e}")
                continue

            version = data.get("schema_version")
            if version != SCHEMA_VERSION:
                report(f"patch db: {name}: schema_version {version!r} is not "
                       f"supported (expected {SCHEMA_VERSION}), file skipped")
                continue

            for raw in data.get("patches", []):
                try:
                    entry = validate_entry(raw, source=name)
                except PatchDBError as e:
                    report(f"patch db: {e}")
                    continue
                db[(entry["crc1"], entry["crc2"])] = entry
    return db


def entries_providing(db: dict[tuple[int, int], dict[str, Any]],
                      capability: str) -> list[dict[str, Any]]:
    """Every entry advertising *capability*, sorted by id."""
    return sorted((e for e in db.values() if capability in e["provides"]),
                  key=lambda e: e["id"])


def describe(db: dict[tuple[int, int], dict[str, Any]]) -> str:
    """Human-readable listing, used by `n64patcher --list-patches`."""
    if not db:
        return "No patch recipes loaded."
    lines = [f"{len(db)} patch recipe(s):", ""]
    for entry in sorted(db.values(), key=lambda e: e["id"]):
        caps = ", ".join(entry["provides"]) or "-"
        lines.append(f"  {entry['id']}")
        lines.append(f"    {entry['name']}")
        lines.append(f"    match: {entry['crc1']:08X}/{entry['crc2']:08X}"
                     f"   provides: {caps}")
        ops = ", ".join(op["type"] for op in entry["operations"])
        lines.append(f"    operations: {ops}   from: {entry['origin']}")
        if entry["notes"]:
            lines.append(f"    {entry['notes']}")
        lines.append("")
    lines.append(f"Searched: {os.pathsep.join(patch_dirs())}")
    return "\n".join(lines)


def _cli() -> int:
    problems: list[str] = []
    db = load_patch_db(on_error=problems.append)
    print(describe(db))
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(_cli())
