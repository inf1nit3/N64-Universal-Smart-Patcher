"""Undo manifests: an auditable record of what a patch actually changed.

The pipeline never modifies an original, so "undo" is not about rescuing a
ROM - it is about answering *what exactly did you change in my file*. That
question matters most for the dynamic VI patcher, which rewrites
instruction patterns it matched rather than applying a hand-verified delta.

A manifest is a JSON sidecar next to the output holding every changed byte
run as ``offset -> (old, new)``, plus the hashes of both files so a later
reader can prove the manifest belongs to them.

Byte runs are only recorded while they stay small. A SubDrag delta rewrites
megabytes, and storing the original bytes for all of that would produce a
sidecar the size of the ROM for no benefit. Past the cap the manifest keeps
the summary and hashes, and says plainly that it cannot revert - an honest
"no" beats a file that looks reversible and is not.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import datdb
from ._version import __version__

MANIFEST_VERSION = 1
MANIFEST_SUFFIX = ".n64patch.json"

#: Stop recording byte runs past this many changed bytes. A delta-patched
#: ROM blows through it immediately; an instruction-mask patch uses a few
#: dozen bytes.
MAX_RECORDED_BYTES = 64 * 1024

_CHUNK = 1024 * 1024


def manifest_path_for(output_path: str) -> str:
    return output_path + MANIFEST_SUFFIX


_BLOCK = 4096


def diff_runs(before: bytes, after: bytes,
              max_bytes: int = MAX_RECORDED_BYTES) -> tuple[list[dict[str, Any]], int, bool]:
    """Contiguous differing runs between two images.

    Returns (runs, changed_bytes, complete). *complete* is False when the
    cap was hit; the run list is then a prefix and *changed_bytes* is a
    lower bound rather than the true total.

    Compared block-first: a ROM is overwhelmingly identical to its patched
    self, and a byte-at-a-time walk over 32 MB in Python is far too slow to
    run per output. Only blocks that actually differ are examined byte by
    byte.
    """
    runs: list[dict[str, Any]] = []
    total = 0
    n = max(len(before), len(after))
    lb, la = len(before), len(after)
    run_start: int | None = None

    def close(end: int) -> bool:
        """Record the open run ending at *end*. False when the cap is hit."""
        nonlocal run_start, total
        assert run_start is not None
        start, run_start = run_start, None
        length = end - start
        total += length
        if total > max_bytes:
            return False
        runs.append({
            "offset": start,
            "old": before[start:min(end, lb)].hex().upper(),
            "new": after[start:min(end, la)].hex().upper(),
        })
        return True

    i = 0
    while i < n:
        j = min(i + _BLOCK, n)
        if before[i:j] == after[i:j]:
            if run_start is not None and not close(i):
                return runs, total, False
            i = j
            continue
        for k in range(i, j):
            same = (before[k] if k < lb else None) == (after[k] if k < la else None)
            if same:
                if run_start is not None and not close(k):
                    return runs, total, False
            elif run_start is None:
                run_start = k
        i = j

    if run_start is not None and not close(n):
        return runs, total, False
    return runs, total, True


def build_manifest(input_path: str, output_path: str,
                   applied: Any = None,
                   stages: list[str] | None = None) -> dict[str, Any]:
    """Describe the difference between an input ROM and its patched output."""
    with open(input_path, "rb") as f:
        before = f.read()
    with open(output_path, "rb") as f:
        after = f.read()

    runs, total, complete = diff_runs(before, after)
    in_hashes = datdb.file_hashes(input_path)
    out_hashes = datdb.file_hashes(output_path)

    return {
        "manifest_version": MANIFEST_VERSION,
        "tool_version": __version__,
        "input": {
            "name": os.path.basename(input_path),
            "size": len(before),
            "sha1": in_hashes["sha1"],
            "crc32": in_hashes["crc32"],
        },
        "output": {
            "name": os.path.basename(output_path),
            "size": len(after),
            "sha1": out_hashes["sha1"],
            "crc32": out_hashes["crc32"],
        },
        "applied": sorted(applied or ()),
        "stages": list(stages or ()),
        "changed_bytes": total,
        "changed_runs": len(runs),
        "revertible": complete,
        "revert_note": "" if complete else (
            f"Change set exceeds {MAX_RECORDED_BYTES} bytes, so the original "
            f"bytes were not stored. Keep the input ROM; it was never modified."),
        "runs": runs,
    }


def write_manifest(manifest: dict[str, Any], output_path: str) -> str:
    path = manifest_path_for(output_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    return path


def load_manifest(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{os.path.basename(path)}: not a manifest object")
    version = data.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise ValueError(
            f"{os.path.basename(path)}: manifest_version {version!r} is not "
            f"supported (expected {MANIFEST_VERSION})")
    return data


def revert(patched_path: str, manifest: dict[str, Any],
           out_path: str) -> tuple[bool, str]:
    """Undo a manifest's changes, writing the original to *out_path*.

    Refuses rather than guessing when the manifest does not describe this
    file, or when it never held the bytes needed to undo.
    """
    if not manifest.get("revertible"):
        return False, (manifest.get("revert_note")
                       or "Manifest does not contain the original bytes")

    with open(patched_path, "rb") as f:
        data = bytearray(f.read())

    expect = manifest.get("output", {})
    actual = datdb.file_hashes(patched_path)
    if expect.get("sha1") and expect["sha1"] != actual["sha1"]:
        return False, (
            f"Manifest describes a different file "
            f"(expected sha1 {expect['sha1'][:16]}..., got {actual['sha1'][:16]}...)")

    for run in manifest.get("runs", []):
        offset = run["offset"]
        old = bytes.fromhex(run["old"])
        new = bytes.fromhex(run["new"])
        if data[offset:offset + len(new)] != new:
            return False, (
                f"Byte run at 0x{offset:X} does not match the manifest; "
                f"the file has been modified since it was patched")
        data[offset:offset + len(old)] = old

    target_size = manifest.get("input", {}).get("size")
    if isinstance(target_size, int) and target_size != len(data):
        del data[target_size:]

    with open(out_path, "wb") as f:
        f.write(bytes(data))

    want = manifest.get("input", {}).get("sha1")
    if want:
        got = datdb.file_hashes(out_path)["sha1"]
        if got != want:
            return False, (f"Reverted file does not match the recorded original "
                           f"(sha1 {got[:16]}... != {want[:16]}...)")
    return True, f"Reverted to {os.path.basename(out_path)} (sha1 verified)"


def describe(manifest: dict[str, Any], max_runs: int = 20) -> str:
    """Human-readable summary, used by `n64patcher --show-manifest`."""
    lines = [
        f"{manifest['input']['name']}  ->  {manifest['output']['name']}",
        f"  tool         : v{manifest.get('tool_version', '?')}",
        f"  applied      : {', '.join(manifest.get('applied') or []) or '-'}",
        f"  stages       : {', '.join(manifest.get('stages') or []) or '-'}",
        f"  changed      : {manifest['changed_bytes']} byte(s) "
        f"in {manifest['changed_runs']} run(s)",
        f"  revertible   : {'yes' if manifest.get('revertible') else 'no'}",
    ]
    if manifest.get("revert_note"):
        lines.append(f"  note         : {manifest['revert_note']}")
    runs = manifest.get("runs", [])
    if runs:
        lines.append("  changes:")
        for run in runs[:max_runs]:
            lines.append(f"    0x{run['offset']:08X}  {run['old']} -> {run['new']}")
        if len(runs) > max_runs:
            lines.append(f"    ... and {len(runs) - max_runs} more")
    return "\n".join(lines)
