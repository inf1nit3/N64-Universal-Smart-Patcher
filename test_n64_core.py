"""Unit tests for n64_core using synthetic ROM images. No real ROMs needed."""

import os
import tempfile
import unittest

import n64_core as core


def make_synthetic_rom(title=b"TEST GAME", country=b"E", game_code=b"NTSE",
                       crc1=0xDEADBEEF, crc2=0x12345678, vi_tables=1, size=0x2000):
    """Build a minimal big-endian z64 image with a valid header and an
    optional synthetic OSViMode table (width 320 + NTSC burst)."""
    rom = bytearray(size)
    rom[0:4] = bytes.fromhex("80371240")
    rom[4:8] = (0x0000000F).to_bytes(4, "big")          # clock rate
    rom[8:12] = (0x80000400).to_bytes(4, "big")         # entry point
    rom[16:20] = crc1.to_bytes(4, "big")
    rom[20:24] = crc2.to_bytes(4, "big")
    rom[32:32 + 20] = title.ljust(20, b" ")[:20]
    rom[59:61] = game_code[:2]
    rom[62:63] = country
    for i in range(vi_tables):
        off = 0x1000 + i * 0x40
        rom[off:off + 4] = core.WIDTH_320_DATA
        rom[off + 4:off + 8] = core.NTSC_BURST
    return bytes(rom)


def byteswap_halfwords(data):
    ba = bytearray(data)
    ba[0::2], ba[1::2] = bytes(ba[1::2]), bytes(ba[0::2])
    return bytes(ba)


def byteswap_words(data):
    ba = bytearray(data)
    ba[0::4], ba[1::4], ba[2::4], ba[3::4] = (
        bytes(ba[3::4]), bytes(ba[2::4]), bytes(ba[1::4]), bytes(ba[0::4]))
    return bytes(ba)


def make_cic6102_rom(size=0x2000):
    """Synthetic ROM whose bootcode word sum equals the CIC-6102 constant
    0xD057C85244: 208 words of 0xFFFFFFFF plus one word of 0x57C85314
    (0xD0 * (2^32 - 1) + 0x57C85314 == 0xD057C85244)."""
    rom = bytearray(make_synthetic_rom(vi_tables=0, size=size))
    for i in range(208):
        off = 0x40 + i * 4
        rom[off:off + 4] = b"\xff\xff\xff\xff"
    rom[0x380:0x384] = bytes.fromhex("57C85314")
    return bytes(rom)


class TestFormatDetection(unittest.TestCase):
    def test_z64_magic(self):
        fmt, _ = core.detect_format(make_synthetic_rom())
        self.assertEqual(fmt, "z64")

    def test_v64_magic(self):
        fmt, _ = core.detect_format(byteswap_halfwords(make_synthetic_rom()))
        self.assertEqual(fmt, "v64")

    def test_n64_magic(self):
        fmt, _ = core.detect_format(byteswap_words(make_synthetic_rom()))
        self.assertEqual(fmt, "n64")

    def test_unknown_magic_rejected(self):
        fmt, _ = core.detect_format(b"\x00\x11\x22\x33" + b"\x00" * 100)
        self.assertIsNone(fmt)

    def test_short_file_rejected(self):
        fmt, _ = core.detect_format(b"\x80")
        self.assertIsNone(fmt)


class TestEndianConversion(unittest.TestCase):
    def test_v64_roundtrip(self):
        z64 = make_synthetic_rom()
        v64 = byteswap_halfwords(z64)
        self.assertEqual(core.to_big_endian(v64, "v64"), z64)

    def test_n64_roundtrip(self):
        z64 = make_synthetic_rom()
        n64 = byteswap_words(z64)
        self.assertEqual(core.to_big_endian(n64, "n64"), z64)

    def test_z64_passthrough(self):
        z64 = make_synthetic_rom()
        self.assertEqual(core.to_big_endian(z64, "z64"), z64)

    def test_odd_length_v64_does_not_crash(self):
        z64 = make_synthetic_rom()
        v64_odd = byteswap_halfwords(z64) + b"\xAA"
        out = core.to_big_endian(v64_odd, "v64")
        self.assertEqual(out[:len(z64)], z64)
        self.assertEqual(out[-1], 0xAA)

    def test_odd_length_n64_does_not_crash(self):
        z64 = make_synthetic_rom()
        n64_odd = byteswap_words(z64) + b"\xAA\xBB"
        out = core.to_big_endian(n64_odd, "n64")
        self.assertEqual(out[:len(z64)], z64)
        self.assertEqual(out[-2:], b"\xAA\xBB")


class TestEnsureZ64(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_converts_v64(self):
        z64 = make_synthetic_rom()
        src = self._write("game.v64", byteswap_halfwords(z64))
        out = os.path.join(self.tmp.name, "out.z64")
        self.assertTrue(core.ensure_z64(src, out))
        with open(out, "rb") as f:
            self.assertEqual(f.read(), z64)

    def test_rejects_unknown_magic(self):
        src = self._write("not_a_rom.z64", b"\xDE\xAD\xBE\xEF" * 100)
        out = os.path.join(self.tmp.name, "out.z64")
        self.assertFalse(core.ensure_z64(src, out))


class TestViTableEngine(unittest.TestCase):
    def test_find_tables(self):
        rom = make_synthetic_rom(vi_tables=2)
        tables = core.find_vi_tables(rom)
        self.assertEqual(len(tables), 2)
        self.assertTrue(all(t["tv"] == "NTSC" for t in tables))

    def test_ignores_width_without_burst(self):
        rom = bytearray(make_synthetic_rom(vi_tables=0))
        rom[0x800:0x804] = core.WIDTH_320_DATA
        rom[0x804:0x808] = b"\xCA\xFE\xBA\xBE"  # not a burst constant
        self.assertEqual(core.find_vi_tables(bytes(rom)), [])

    def test_pal_and_mpal_detected(self):
        rom = bytearray(make_synthetic_rom(vi_tables=0))
        rom[0x900:0x904] = core.WIDTH_320_DATA
        rom[0x904:0x908] = core.PAL_BURST
        rom[0x940:0x944] = core.WIDTH_320_DATA
        rom[0x944:0x948] = core.MPAL_BURST
        tvs = {t["tv"] for t in core.find_vi_tables(bytes(rom))}
        self.assertEqual(tvs, {"PAL", "M-PAL"})

    def test_hires_patch_only_touches_tables(self):
        rom = make_synthetic_rom(vi_tables=2)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "game.z64")
            with open(p, "wb") as f:
                f.write(rom)
            ok, count, _ = core.apply_smart_hires_patch(p)
            self.assertTrue(ok)
            self.assertEqual(count, 2)
            with open(p, "rb") as f:
                patched = f.read()
            self.assertEqual(patched[0x1000:0x1004], core.WIDTH_640_DATA)
            self.assertEqual(patched[0x1040:0x1044], core.WIDTH_640_DATA)
            # Everything except the two width words is untouched
            for off in (0x1000, 0x1040):
                rom = rom[:off] + core.WIDTH_640_DATA + rom[off + 4:]
            self.assertEqual(patched, rom)

    def test_hires_patch_no_tables(self):
        rom = make_synthetic_rom(vi_tables=0)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "game.z64")
            with open(p, "wb") as f:
                f.write(rom)
            ok, count, _ = core.apply_smart_hires_patch(p)
            self.assertFalse(ok)
            self.assertEqual(count, 0)


class TestDynamicViPatch(unittest.TestCase):
    def _rom_with_patterns(self):
        rom = bytearray(make_synthetic_rom(vi_tables=0))
        rom[0x500:0x504] = bytes.fromhex("31cf0040")   # dither mask
        rom[0x504:0x508] = bytes.fromhex("11e0000d")   # companion branch
        rom[0x600:0x604] = bytes.fromhex("30423000")   # AA mask
        return bytes(rom)

    def _run(self, data, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = os.path.join(tmp.name, "game.z64")
        with open(p, "wb") as f:
            f.write(data)
        applied = core.apply_dynamic_vi_patch(p, **kwargs)
        with open(p, "rb") as f:
            return applied, f.read()

    def test_both_patterns(self):
        applied, out = self._run(self._rom_with_patterns())
        self.assertEqual(applied, {"NoAA", "NoDither"})
        self.assertEqual(out[0x500:0x504], bytes.fromhex("31cf0000"))
        self.assertEqual(out[0x504:0x508], bytes.fromhex("1000000d"))
        self.assertEqual(out[0x600:0x604], bytes.fromhex("30422000"))

    def test_dither_only(self):
        applied, out = self._run(self._rom_with_patterns(), no_aa=False)
        self.assertEqual(applied, {"NoDither"})
        self.assertEqual(out[0x600:0x604], bytes.fromhex("30423000"))  # AA untouched

    def test_aa_only(self):
        applied, out = self._run(self._rom_with_patterns(), no_dither=False)
        self.assertEqual(applied, {"NoAA"})
        self.assertEqual(out[0x500:0x504], bytes.fromhex("31cf0040"))  # dither untouched

    def test_nothing_requested(self):
        applied, out = self._run(self._rom_with_patterns(), no_aa=False, no_dither=False)
        self.assertEqual(applied, set())
        self.assertEqual(out, self._rom_with_patterns())


class TestInspection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_header_fields(self):
        rom = make_synthetic_rom(title=b"COOL GAME", country=b"P", vi_tables=3)
        p = self._write("cool.z64", rom)
        info = core.inspect_rom_details(p)
        self.assertEqual(info["title"], "COOL GAME")
        self.assertEqual(info["region"], core.REGION_MAP["P"])
        self.assertEqual(info["crc1"], "DEADBEEF")
        self.assertEqual(info["crc2"], "12345678")
        self.assertEqual(info["vi_table_count"], 3)
        self.assertFalse(info["is_hires_640x480"])

    def test_hires_detection(self):
        rom = bytearray(make_synthetic_rom(vi_tables=0))
        rom[0x1000:0x1004] = core.WIDTH_640_DATA
        rom[0x1004:0x1008] = core.NTSC_BURST
        p = self._write("hires.z64", bytes(rom))
        info = core.inspect_rom_details(p)
        self.assertTrue(info["is_hires_640x480"])
        self.assertEqual(info["vi_table_count"], 0)

    def test_v64_header(self):
        z64 = make_synthetic_rom(title=b"SWAPPED", country=b"J")
        p = self._write("swapped.v64", byteswap_halfwords(z64))
        info = core.inspect_rom_details(p)
        self.assertEqual(info["title"], "SWAPPED")
        self.assertEqual(info["region"], core.REGION_MAP["J"])

    def test_hashes(self):
        import hashlib
        rom = make_synthetic_rom()
        p = self._write("hashed.z64", rom)
        info = core.inspect_rom_details(p, with_hashes=True)
        self.assertEqual(info["md5"], hashlib.md5(rom).hexdigest().upper())
        self.assertEqual(info["sha1"], hashlib.sha1(rom).hexdigest().upper())

    def test_garbage_file(self):
        p = self._write("junk.z64", b"\xFF" * 100)
        info = core.inspect_rom_details(p)
        self.assertIn("Unknown", info["format"])
        self.assertEqual(info["title"], "Unknown")


class TestHelpers(unittest.TestCase):
    def test_is_tool_output(self):
        self.assertTrue(core.is_tool_output("game [NoAA].z64"))
        self.assertTrue(core.is_tool_output("game [HR+NoAA].z64"))
        self.assertTrue(core.is_tool_output("game.z64.temp.z64"))
        self.assertFalse(core.is_tool_output("game.z64"))
        self.assertFalse(core.is_tool_output("game [NoAA].txt"))

    def test_build_output_path_tags(self):
        p = core.build_output_path("/roms/game.z64", {"HR", "NoAA"})
        self.assertTrue(p.endswith("game [HR+NoAA].z64"))
        p = core.build_output_path("/roms/game.z64", {"HR"})
        self.assertTrue(p.endswith("game [640p].z64"))
        p = core.build_output_path("/roms/game.z64", {"NoDither"})
        self.assertTrue(p.endswith("game [NoDither].z64"))

    def test_build_output_path_strips_old_tag(self):
        p = core.build_output_path("/roms/game [NoAA].z64", {"NoAA"})
        self.assertNotEqual(os.path.abspath(p), os.path.abspath("/roms/game [NoAA].z64"))
        self.assertIn("(2)", p)

    def test_build_output_path_long_name_capped(self):
        long_name = "A" * 100 + ".z64"
        p = core.build_output_path(f"/roms/{long_name}", {"NoAA"})
        self.assertLessEqual(len(os.path.basename(p)), 65 + len(".z64"))


class TestPatchPipeline(unittest.TestCase):
    """End-to-end on synthetic ROMs. External tools (u64aap/rn64crc/xdelta)
    may be absent in CI - the pipeline must degrade gracefully and still
    produce patched output via the dynamic/table engines."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_hires_pipeline_creates_tagged_output_and_preserves_original(self):
        rom = make_synthetic_rom(vi_tables=2)
        src = self._write("game.z64", rom)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        logs = []
        res = core.patch_rom(src, opts, log=logs.append)
        self.assertEqual(res["status"], "patched")
        self.assertIn("HR", res["applied"])
        self.assertTrue(res["output"].endswith(" [640p].z64"))
        with open(src, "rb") as f:
            self.assertEqual(f.read(), rom)  # original untouched
        with open(res["output"], "rb") as f:
            out = f.read()
        self.assertEqual(out[0x1000:0x1004], core.WIDTH_640_DATA)

    def test_rejects_non_rom(self):
        src = self._write("fake.z64", b"\x00" * 1024)
        res = core.patch_rom(src, core.PatchOptions(), log=lambda m: None)
        self.assertEqual(res["status"], "skipped")
        self.assertIn("magic", res["message"])

    def test_skip_when_nothing_patchable(self):
        rom = make_synthetic_rom(vi_tables=0)
        src = self._write("bare.z64", rom)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        res = core.patch_rom(src, opts, log=lambda m: None)
        self.assertEqual(res["status"], "skipped")

    def test_cancel_aborts_run(self):
        rom = make_synthetic_rom(vi_tables=1)
        src = self._write("cancelme.z64", rom)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        res = core.patch_rom(src, opts, log=lambda m: None, should_cancel=lambda: True)
        self.assertEqual(res["status"], "cancelled")
        # No temp files left behind
        leftovers = [f for f in os.listdir(self.tmp.name) if f != "cancelme.z64"]
        self.assertEqual(leftovers, [])

    @unittest.skipUnless(core._is_runnable(core.XDELTA3_PATH),
                         "no runnable xdelta3 available (bundled exe not "
                         "executable on this platform, no system xdelta3)")
    def test_subdrag_xdelta_applies_to_clean_source_first(self):
        import subprocess
        from unittest import mock

        # Clean source ROM titled to match a SubDrag entry
        clean = make_synthetic_rom(title=b"SUPER MARIO 64", vi_tables=1)
        src = self._write("sm64.z64", clean)

        # Target = same ROM but hi-res width + extra modification (proves the
        # delta content lands, not the Smart VI fallback)
        target = bytearray(clean)
        target[0x1000:0x1004] = core.WIDTH_640_DATA
        target[0x2000 - 1] = 0x42
        target_path = self._write("target.bin", bytes(target))

        delta = os.path.join(self.tmp.name, "sm64_hires.xdelta")
        mk = subprocess.run([core.XDELTA3_PATH, "-e", "-s", src, target_path, delta],
                            capture_output=True, creationflags=core.CREATE_NO_WINDOW)
        self.assertEqual(mk.returncode, 0, mk.stderr.decode(errors="replace"))

        fake_dir = os.path.join(self.tmp.name, "fake_patches")
        os.mkdir(fake_dir)
        import shutil
        fake_delta = os.path.join(fake_dir, "sm64 NoAA hires.xdelta")
        shutil.copy(delta, fake_delta)

        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        logs = []
        with mock.patch.object(core, "HIRES_PATCHES_DIR", fake_dir), \
             mock.patch.object(core, "SUBDRAG_PATCHES", {"SUPER MARIO 64": "sm64 NoAA hires.xdelta"}):
            res = core.patch_rom(src, opts, log=logs.append)

        self.assertEqual(res["status"], "patched")
        self.assertIn("HR", res["applied"])
        self.assertIn("NoAA", res["applied"])  # NoAA-variant patch -> tag
        self.assertTrue(any("SubDrag" in line for line in logs))
        with open(res["output"], "rb") as f:
            out = f.read()
        self.assertEqual(out[0x1000:0x1004], core.WIDTH_640_DATA)
        self.assertEqual(out[0x2000 - 1], 0x42)  # delta content, not fallback engine
        with open(src, "rb") as f:
            self.assertEqual(f.read(), clean)  # original untouched


class TestCrcEngine(unittest.TestCase):
    """Pure-Python N64 boot CRC engine (emulator-compatible algorithm)."""

    def _make_cic6102_rom(self):
        return make_cic6102_rom()

    def test_detect_cic_6102(self):
        self.assertEqual(core.detect_cic_chip(self._make_cic6102_rom()), "6102")

    def test_detect_cic_unknown(self):
        self.assertIsNone(core.detect_cic_chip(make_synthetic_rom(vi_tables=0)))

    def test_crc_deterministic(self):
        rom = self._make_cic6102_rom()
        first = core.calculate_n64_crc(rom)
        second = core.calculate_n64_crc(rom)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    def test_fix_native_stamps_consistent_crc(self):
        rom = self._make_cic6102_rom()
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "crc.z64")
            with open(p, "wb") as f:
                f.write(rom)
            ok, msg = core.fix_rom_crc_native(p)
            self.assertTrue(ok, msg)
            with open(p, "rb") as f:
                stamped = f.read()
            expected = core.calculate_n64_crc(stamped)
            self.assertIsNotNone(expected)
            self.assertEqual(stamped[0x10:0x14], expected[0].to_bytes(4, "big"))
            self.assertEqual(stamped[0x14:0x18], expected[1].to_bytes(4, "big"))

    def test_fix_native_rejects_swapped_format(self):
        swapped = byteswap_halfwords(self._make_cic6102_rom())
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "crc.v64")
            with open(p, "wb") as f:
                f.write(swapped)
            ok, msg = core.fix_rom_crc_native(p)
            self.assertFalse(ok)
            self.assertIn("big-endian", msg)

    def test_fix_native_rejects_unknown_cic(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "nocic.z64")
            with open(p, "wb") as f:
                f.write(make_synthetic_rom(vi_tables=0))
            ok, msg = core.fix_rom_crc_native(p)
            self.assertFalse(ok)
            self.assertIn("Unknown CIC", msg)


class TestOutputDirAndTags(unittest.TestCase):
    def test_build_output_path_output_dir(self):
        p = core.build_output_path("/roms/game.z64", {"NoAA"}, output_dir="/tmp/out")
        self.assertEqual(os.path.dirname(p), "/tmp/out")
        self.assertTrue(p.endswith("game [NoAA].z64"))

    def test_patch_rom_writes_to_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_synthetic_rom(vi_tables=1)
            src = os.path.join(tmp, "game.z64")
            with open(src, "wb") as f:
                f.write(rom)
            outdir = os.path.join(tmp, "out", "nested")
            opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
            res = core.patch_rom(src, opts, log=lambda m: None, output_dir=outdir)
            self.assertEqual(res["status"], "patched")
            self.assertEqual(os.path.dirname(res["output"]), outdir)
            self.assertTrue(os.path.isfile(res["output"]))
            self.assertEqual(res["input"], src)
            with open(src, "rb") as f:
                self.assertEqual(f.read(), rom)  # original untouched


class TestToolingHelpers(unittest.TestCase):
    def test_is_tool_output_new_tags(self):
        self.assertTrue(core.is_tool_output("game.z64.stripped.z64"))
        self.assertTrue(core.is_tool_output("game [COMMUNITY].z64"))
        self.assertTrue(core.is_tool_output("game [CRCFIX].z64"))

    def test_version_present(self):
        self.assertIsInstance(core.VERSION, str)
        self.assertEqual(core.VERSION.count("."), 2)

    def test_patch_options_has_no_applied_tags(self):
        self.assertNotIn("applied_tags", core.PatchOptions.__dataclass_fields__)

    def test_check_tools_reports_native_crc(self):
        self.assertTrue(core.check_tools()["crc_native"])


class TestInspectionPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_z64_fast_path_fields(self):
        rom = make_synthetic_rom(title=b"FASTPATH", country=b"P", vi_tables=2)
        p = self._write("fast.z64", rom)
        info = core.inspect_rom_details(p, with_hashes=True)
        self.assertEqual(info["title"], "FASTPATH")
        self.assertEqual(info["region"], core.REGION_MAP["P"])
        self.assertEqual(info["crc1"], "DEADBEEF")
        self.assertEqual(info["vi_table_count"], 2)
        self.assertIn("md5", info)

    def test_v64_slow_path_fields(self):
        v64 = byteswap_halfwords(make_synthetic_rom(title=b"SWAPPED2", country=b"J"))
        p = self._write("slow.v64", v64)
        info = core.inspect_rom_details(p)
        self.assertEqual(info["title"], "SWAPPED2")
        self.assertEqual(info["region"], core.REGION_MAP["J"])
        self.assertEqual(info["vi_table_count"], 1)

    def test_mmap_scanner_matches_memory_scan(self):
        rom = make_synthetic_rom(vi_tables=3)
        p = self._write("scan.z64", rom)
        self.assertEqual(core.scan_vi_tables_file(p), core.find_vi_tables(rom))
        self.assertEqual(core.scan_vi_tables_file(p, core.WIDTH_640_DATA), [])


if __name__ == "__main__":
    unittest.main()
