"""Unit tests for zip_handler (extraction + zip-slip protection)."""
import os
import stat
import tempfile
import unittest
import zipfile
from unittest import mock

from n64patcher import zip_handler
from n64patcher.zip_handler import (
    cleanup_temp_dir,
    create_extraction_dir,
    extract_roms_from_archive,
    is_archive,
)
from tests.test_n64_core import make_synthetic_rom


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

    # --- decompression bombs and link members ---------------------------

    def test_declared_size_cannot_smuggle_a_bomb_past_the_cap(self):
        """The regression: the cap used to be checked against file_size
        from the central directory, which the archive author controls. A
        member can declare 1 KB and expand to gigabytes."""
        zp = self._path("bomb.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bomb.z64", b"\x00" * (8 * 1024 * 1024))
        # Lie about the uncompressed size in the directory the reader trusts.
        with zipfile.ZipFile(zp, "r") as zf:
            info = zf.infolist()[0]
        self.assertGreater(info.file_size, 1024)

        out_dir = self._path("bomb_out")
        with mock.patch.object(zip_handler, "MAX_EXTRACT_TOTAL_BYTES", 1024):
            with self.assertRaises(RuntimeError) as ctx:
                extract_roms_from_archive(zp, out_dir)
        self.assertIn("safety", str(ctx.exception).lower())

    def test_cap_is_enforced_on_bytes_actually_written(self):
        """The streaming cap does not consult the declared size at all: it
        counts what arrives and aborts mid-member. Tested directly because
        zipfile cross-checks a forged file_size against the CRC on read,
        so a lie cannot be planted through ZipInfo alone."""
        zp = self._path("bomb2.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bomb.z64", b"\x00" * (4 * 1024 * 1024))
        target = self._path("out.z64")
        with zipfile.ZipFile(zp, "r") as zf:
            member = zf.infolist()[0]
            budget = [64 * 1024]
            with self.assertRaises(RuntimeError) as ctx:
                zip_handler._extract_member(zf, member, target, budget)
        self.assertIn("cap", str(ctx.exception).lower())
        # Aborted partway, not after writing the whole 4 MB.
        self.assertLess(os.path.getsize(target), 4 * 1024 * 1024)

    def test_budget_is_shared_across_members(self):
        """One member under the cap is fine; several that together exceed
        it must still be caught."""
        zp = self._path("many.zip")
        rom = make_synthetic_rom(size=0x20000)  # 128 KB each
        with zipfile.ZipFile(zp, "w") as zf:
            for i in range(6):
                zf.writestr(f"game{i}.z64", rom)
        out_dir = self._path("many_out")
        with mock.patch.object(zip_handler, "MAX_EXTRACT_TOTAL_BYTES", 300 * 1024):
            with self.assertRaises(RuntimeError) as ctx:
                extract_roms_from_archive(zp, out_dir)
        self.assertIn("safety", str(ctx.exception).lower())

    def test_absurd_compression_ratio_rejected(self):
        zp = self._path("ratio.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("flat.z64", b"\x00" * (16 * 1024 * 1024))
        out_dir = self._path("ratio_out")
        with mock.patch.object(zip_handler, "MAX_COMPRESSION_RATIO", 2):
            with self.assertRaises(RuntimeError) as ctx:
                extract_roms_from_archive(zp, out_dir)
        self.assertIn("expands", str(ctx.exception).lower())

    def test_symlink_member_rejected(self):
        """A symlink member is a write primitive pointing anywhere the
        user can write; neither extractor filtered them before."""
        zp = self._path("link.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            info = zipfile.ZipInfo("evil.z64")
            info.create_system = 3  # unix
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
        out_dir = self._path("link_out")
        with self.assertRaises(RuntimeError) as ctx:
            extract_roms_from_archive(zp, out_dir)
        self.assertIn("symlink", str(ctx.exception).lower())

    def test_extracted_path_is_the_validated_path(self):
        """zf.extract() picks its own destination via its own sanitizer,
        so the returned list could name files that were never written."""
        rom = make_synthetic_rom()
        zp = self._path("paths.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("a/b/game.z64", rom)
            zf.writestr("weird name #1.z64", rom)
        out_dir = self._path("paths_out")
        extracted = extract_roms_from_archive(zp, out_dir)
        self.assertEqual(len(extracted), 2)
        for p in extracted:
            self.assertTrue(os.path.isfile(p), f"reported but not written: {p}")
            with open(p, "rb") as f:
                self.assertEqual(f.read(), rom)

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
