"""Unit tests for zip_handler (extraction + zip-slip protection)."""
import os
import tempfile
import unittest
import zipfile

from zip_handler import (is_archive, extract_roms_from_archive,
                         cleanup_temp_dir, create_extraction_dir)
from test_n64_core import make_synthetic_rom


class TestZipHandler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_is_archive_checks_file(self):
        zp = self._path("roms.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("x.z64", b"\x00")
        self.assertTrue(is_archive(zp))
        self.assertFalse(is_archive(self._path("missing.zip")))
        os.makedirs(self._path("folder.zip"), exist_ok=True)
        self.assertFalse(is_archive(self._path("folder.zip")))  # dir is not an archive
        self.assertFalse(is_archive(self._path("roms.rar")))

    def test_extract_roms_nested(self):
        rom = make_synthetic_rom()
        zp = self._path("roms.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("subdir/game.z64", rom)
            zf.writestr("readme.txt", b"not a rom")
        out_dir = self._path("extract")
        extracted = extract_roms_from_archive(zp, out_dir)
        self.assertEqual(len(extracted), 1)
        with open(extracted[0], "rb") as f:
            self.assertEqual(f.read(), rom)

    def test_zip_slip_blocked(self):
        zp = self._path("evil.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("../evil.z64", make_synthetic_rom())
        out_dir = self._path("extract2")
        with self.assertRaises(RuntimeError) as ctx:
            extract_roms_from_archive(zp, out_dir)
        self.assertIn("traversal", str(ctx.exception).lower())
        self.assertFalse(os.path.exists(self._path("evil.z64")))

    def test_absolute_member_blocked(self):
        zp = self._path("abs.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("/tmp/abs_evil.z64", make_synthetic_rom())
        out_dir = self._path("extract3")
        # zipfile itself strips leading '/', our validator sees a safe path;
        # either way nothing may land outside out_dir
        try:
            extracted = extract_roms_from_archive(zp, out_dir)
            for p in extracted:
                self.assertTrue(os.path.realpath(p).startswith(
                    os.path.realpath(out_dir) + os.sep))
        except RuntimeError:
            pass  # rejected outright - also acceptable
        self.assertFalse(os.path.exists("/tmp/abs_evil.z64"))

    def test_missing_archive_raises(self):
        with self.assertRaises(RuntimeError):
            extract_roms_from_archive(self._path("nope.zip"), self._path("x"))

    def test_create_and_cleanup_extraction_dir(self):
        d = create_extraction_dir()
        self.assertTrue(os.path.isdir(d))
        self.assertIn("n64patch_extract_", os.path.basename(d))
        cleanup_temp_dir(d)
        self.assertFalse(os.path.exists(d))

    @unittest.skipUnless(__import__("importlib").util.find_spec("py7zr"),
                         "py7zr not installed")
    def test_7z_zip_slip_blocked(self):
        import py7zr
        inner = self._path("x.z64")
        with open(inner, "wb") as f:
            f.write(make_synthetic_rom())
        zp = self._path("evil.7z")
        with py7zr.SevenZipFile(zp, "w") as sz:
            sz.write(inner, arcname="../evil7.z64")
        out_dir = self._path("extract7z_slip")
        with self.assertRaises(RuntimeError) as ctx:
            extract_roms_from_archive(zp, out_dir)
        self.assertIn("traversal", str(ctx.exception).lower())
        self.assertFalse(os.path.exists(self._path("evil7.z64")))

    @unittest.skipUnless(__import__("importlib").util.find_spec("py7zr"),
                         "py7zr not installed")
    def test_7z_roundtrip(self):
        import py7zr
        rom = make_synthetic_rom()
        rom_path = self._path("inner.z64")
        with open(rom_path, "wb") as f:
            f.write(rom)
        zp = self._path("roms.7z")
        with py7zr.SevenZipFile(zp, "w") as sz:
            sz.write(rom_path, arcname="inner.z64")
        out_dir = self._path("extract7z")
        extracted = extract_roms_from_archive(zp, out_dir)
        self.assertEqual(len(extracted), 1)
        with open(extracted[0], "rb") as f:
            self.assertEqual(f.read(), rom)


if __name__ == "__main__":
    unittest.main()
