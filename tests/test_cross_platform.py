"""Guards for the assumptions that hold on Windows and break elsewhere.

These tests must be able to fail on a case-insensitive filesystem, otherwise
they only report a problem to the people who do not have it. So nothing here
asks the filesystem "does this path exist?" - the directory is listed once and
names are compared as strings.
"""
import os
import re
import unittest

from n64patcher import n64_core as core
from n64patcher import patchdb

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "n64patcher")

# Windows forbids these in filenames; Linux and macOS do not. A shipped asset
# named with one of them is unpackable on Windows.
WINDOWS_RESERVED = set('<>:"|?*\\')
WINDOWS_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def bundled_patch_entries():
    """The recipes that actually ship, not a synthetic fixture."""
    problems = []
    db = patchdb.load_patch_db([os.path.join(SRC, "patches")],
                               on_error=problems.append)
    if problems:
        raise AssertionError("bundled patch database has errors: "
                             + "; ".join(problems))
    return list(db.values())


class TestPatchAssetNamesResolveCaseExactly(unittest.TestCase):
    """Every .xdelta named in patches.json must match a real file byte for
    byte, including case. A mismatch is invisible on Windows and NTFS, and
    turns every verified dump into "unsupported" on Linux and macOS.
    """

    def setUp(self):
        self.patch_dir = core.HIRES_PATCHES_DIR
        if not os.path.isdir(self.patch_dir):
            self.skipTest("hires_patches directory not present in this layout")
        self.on_disk = set(os.listdir(self.patch_dir))

    def referenced_files(self):
        for entry in bundled_patch_entries():
            for op in entry.get("operations", []):
                if op.get("type") == "xdelta":
                    yield entry["id"], op["file"]

    def test_database_is_not_empty(self):
        """A load failure would make every other test here vacuously pass."""
        self.assertTrue(list(self.referenced_files()),
                        "no xdelta operations found - did the database load?")

    def test_every_referenced_patch_exists_with_exact_case(self):
        missing = []
        for entry_id, filename in self.referenced_files():
            if filename in self.on_disk:
                continue
            lowered = {n.lower(): n for n in self.on_disk}
            actual = lowered.get(filename.lower())
            if actual:
                missing.append(
                    f"{entry_id}: case mismatch, json says {filename!r} "
                    f"but disk has {actual!r}")
            else:
                missing.append(f"{entry_id}: no such file {filename!r}")
        self.assertEqual(missing, [], "\n".join(missing))

    def test_no_two_assets_differ_only_by_case(self):
        """Such a pair cannot be checked out on Windows at all - one file
        silently overwrites the other."""
        seen = {}
        clashes = []
        for name in self.on_disk:
            key = name.lower()
            if key in seen:
                clashes.append(f"{seen[key]!r} vs {name!r}")
            seen[key] = name
        self.assertEqual(clashes, [], "; ".join(clashes))


class TestShippedAssetNamesArePortable(unittest.TestCase):
    """Assets are named by their upstream authors, so this checks rather than
    assumes that they survive a checkout on every target platform."""

    def asset_names(self):
        for sub in ("N64noAAPatcher/hires_patches",
                    "N64noAAPatcher/additionals",
                    "patches"):
            directory = os.path.join(SRC, *sub.split("/"))
            if not os.path.isdir(directory):
                continue
            for name in os.listdir(directory):
                yield os.path.join(sub, name), name

    def test_no_characters_windows_rejects(self):
        bad = [path for path, name in self.asset_names()
               if WINDOWS_RESERVED & set(name)]
        self.assertEqual(bad, [])

    def test_no_reserved_device_names(self):
        bad = [path for path, name in self.asset_names()
               if name.split(".")[0].upper() in WINDOWS_RESERVED_STEMS]
        self.assertEqual(bad, [])

    def test_no_trailing_space_or_dot(self):
        """Windows silently strips both, so the file lands under a name that
        no longer matches what the database asks for."""
        bad = [path for path, name in self.asset_names()
               if name != name.rstrip(" .")]
        self.assertEqual(bad, [])


class TestNoHardcodedPlatformPaths(unittest.TestCase):
    """A drive letter or backslash path in the source is a Windows-only code
    path that will not announce itself on Windows."""

    DRIVE_LETTER = re.compile(r"['\"][A-Za-z]:[\\/]")

    def source_files(self):
        for root, _dirs, files in os.walk(SRC):
            if "__pycache__" in root:
                continue
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(root, name)

    def test_no_absolute_windows_paths_in_source(self):
        hits = []
        for path in self.source_files():
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if self.DRIVE_LETTER.search(line):
                        hits.append(f"{os.path.basename(path)}:{lineno}")
        self.assertEqual(hits, [], "; ".join(hits))

    def test_no_os_startfile(self):
        """os.startfile does not exist off Windows; opening a file or folder
        has to go through a platform check."""
        hits = []
        for path in self.source_files():
            with open(path, encoding="utf-8") as f:
                if "os.startfile" in f.read():
                    hits.append(os.path.basename(path))
        self.assertEqual(hits, [])


class TestBundledHelpersAreOptional(unittest.TestCase):
    """The three bundled helpers are Windows PE binaries. Off Windows they
    must be reported as unavailable rather than executed - and the engine
    must still work."""

    def test_check_tools_always_reports_the_native_crc_engine(self):
        self.assertTrue(core.check_tools()["crc_native"])

    def test_windows_binaries_are_not_marked_executable(self):
        """If the exec bit were set, _is_runnable would say yes on Linux and
        the tool would try to exec a PE binary."""
        if os.name == "nt":
            self.skipTest("permission bits are not meaningful on Windows")
        directory = os.path.join(SRC, "N64noAAPatcher", "additionals")
        if not os.path.isdir(directory):
            self.skipTest("bundled helpers not present in this layout")
        executable = [n for n in os.listdir(directory)
                      if n.lower().endswith(".exe")
                      and os.access(os.path.join(directory, n), os.X_OK)]
        self.assertEqual(executable, [])

    def test_install_hint_is_offered_for_every_platform(self):
        self.assertTrue(core.xdelta3_install_hint().strip())


if __name__ == "__main__":
    unittest.main()
