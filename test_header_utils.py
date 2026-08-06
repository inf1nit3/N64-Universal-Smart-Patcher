"""Unit tests for header_utils (scene-header strip, CRC fix, header info)."""
import os
import tempfile
import unittest

import n64_core as core
from header_utils import (detect_scene_header, detect_and_strip_scene_header,
                          fix_rom_crc, get_rom_info_from_header)
from test_n64_core import make_synthetic_rom, make_cic6102_rom


class HeaderTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p


class TestSceneHeaderStrip(HeaderTestBase):
    def test_no_header_no_copy_created(self):
        rom = make_synthetic_rom()
        src = self._write("clean.z64", rom)
        out = os.path.join(self.tmp.name, "clean.stripped.z64")
        res = detect_and_strip_scene_header(src, out)
        self.assertFalse(res["stripped"])
        self.assertEqual(res["header_size"], 0)
        self.assertFalse(os.path.exists(out),
                         "no output file may be created when nothing is stripped")

    def test_strip_512(self):
        rom = make_synthetic_rom()
        prefixed = b"iN0000" + b"\x00" * (512 - 6) + rom
        src = self._write("scene512.z64", prefixed)
        out = os.path.join(self.tmp.name, "stripped.z64")
        res = detect_and_strip_scene_header(src, out)
        self.assertTrue(res["stripped"])
        self.assertEqual(res["header_size"], 512)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), rom)

    def test_strip_1024(self):
        rom = make_synthetic_rom()
        prefixed = b"PARADOX!" + b"\xAA" * (1024 - 8) + rom
        src = self._write("scene1024.z64", prefixed)
        out = os.path.join(self.tmp.name, "stripped.z64")
        res = detect_and_strip_scene_header(src, out)
        self.assertTrue(res["stripped"])
        self.assertEqual(res["header_size"], 1024)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), rom)

    def test_detect_sizes(self):
        rom = make_synthetic_rom()
        self.assertEqual(detect_scene_header(self._write("a.z64", rom)), 0)
        self.assertEqual(detect_scene_header(
            self._write("b.z64", b"\x00" * 512 + rom)), 512)
        self.assertEqual(detect_scene_header(
            self._write("c.z64", b"\x00" * 896 + rom)), 896)

    def test_unknown_format(self):
        src = self._write("junk.z64", b"\x00" * 2000)
        out = os.path.join(self.tmp.name, "junk.out")
        res = detect_and_strip_scene_header(src, out)
        self.assertFalse(res["stripped"])
        self.assertIn("Unbekanntes ROM-Format", res["message"])
        self.assertFalse(os.path.exists(out))


class TestGetRomInfo(HeaderTestBase):
    def test_fields_from_clean_rom(self):
        rom = bytearray(make_synthetic_rom(title=b"INFO GAME", country=b"E"))
        rom[0x3F] = 0x42  # version byte
        src = self._write("info.z64", bytes(rom))
        info = get_rom_info_from_header(src)
        self.assertEqual(info["title"], "INFO GAME")
        self.assertEqual(info["crc1"], "DEADBEEF")
        self.assertEqual(info["crc2"], "12345678")
        self.assertEqual(info["version"], 0x42)          # 0x3F = version
        self.assertEqual(info["region"], core.REGION_MAP["E"])  # 0x3E = country

    def test_fields_behind_scene_header(self):
        rom = make_synthetic_rom(title=b"HIDDEN GAME", country=b"J")
        src = self._write("hidden.z64", b"\x00" * 512 + rom)
        info = get_rom_info_from_header(src)
        self.assertEqual(info["title"], "HIDDEN GAME")
        self.assertEqual(info["region"], core.REGION_MAP["J"])


class TestFixRomCrc(HeaderTestBase):
    def test_native_fallback_when_tool_missing(self):
        rom = make_cic6102_rom()
        src = self._write("crcfix.z64", rom)
        res = fix_rom_crc(src, "/nonexistent/rn64crc")
        self.assertEqual(res["status"], "fixed", res)
        with open(src, "rb") as f:
            stamped = f.read()
        expected = core.calculate_n64_crc(stamped)
        self.assertIsNotNone(expected)
        self.assertEqual(stamped[0x10:0x14], expected[0].to_bytes(4, "big"))
        self.assertEqual(stamped[0x14:0x18], expected[1].to_bytes(4, "big"))

    def test_unknown_cic_reports_error(self):
        src = self._write("nocic.z64", make_synthetic_rom(vi_tables=0))
        res = fix_rom_crc(src, "/nonexistent/rn64crc")
        self.assertEqual(res["status"], "error")


if __name__ == "__main__":
    unittest.main()
