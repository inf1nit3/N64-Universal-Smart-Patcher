"""Unit tests for n64_core using synthetic ROM images. No real ROMs needed."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from n64patcher import n64_core as core


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
    # Both sites sit in the code segment (>= 0x1000) and are word-aligned.
    DITHER_AT = 0x1500
    AA_AT = 0x1600

    def _rom_with_patterns(self, size=0x4000):
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=size))
        rom[self.DITHER_AT:self.DITHER_AT + 4] = core.DITHER_PATTERN
        rom[self.DITHER_AT + 4:self.DITHER_AT + 8] = core.DITHER_BRANCH
        rom[self.AA_AT:self.AA_AT + 4] = core.AA_PATTERN
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
        self.assertEqual(out[self.DITHER_AT:self.DITHER_AT + 4], core.DITHER_REPLACEMENT)
        self.assertEqual(out[self.DITHER_AT + 4:self.DITHER_AT + 8],
                         core.DITHER_BRANCH_REPLACEMENT)
        self.assertEqual(out[self.AA_AT:self.AA_AT + 4], core.AA_REPLACEMENT)

    def test_dither_only(self):
        applied, out = self._run(self._rom_with_patterns(), no_aa=False)
        self.assertEqual(applied, {"NoDither"})
        self.assertEqual(out[self.AA_AT:self.AA_AT + 4], core.AA_PATTERN)

    def test_aa_only(self):
        applied, out = self._run(self._rom_with_patterns(), no_dither=False)
        self.assertEqual(applied, {"NoAA"})
        self.assertEqual(out[self.DITHER_AT:self.DITHER_AT + 4], core.DITHER_PATTERN)

    def test_nothing_requested(self):
        applied, out = self._run(self._rom_with_patterns(), no_aa=False, no_dither=False)
        self.assertEqual(applied, set())
        self.assertEqual(out, self._rom_with_patterns())

    def test_bootcode_is_never_touched(self):
        """Below 0x1000 lives IPL3. Rewriting it changes the word sum that
        detect_cic_chip keys on, so the CRC engine can no longer identify
        the chip - the ROM becomes unfixable, not merely unpatched."""
        rom = bytearray(make_cic6102_rom(size=0x4000))
        rom[0x500:0x504] = core.AA_PATTERN
        rom[0x600:0x604] = core.DITHER_PATTERN
        rom = bytes(rom)
        before = core.detect_cic_chip(rom)
        applied, out = self._run(rom)
        self.assertEqual(applied, set())
        self.assertEqual(out, rom)
        self.assertEqual(core.detect_cic_chip(out), before)

    def test_patching_preserves_cic_identification(self):
        """The complementary case: a real CIC-6102 boot region survives a
        patch that does fire in the code segment."""
        rom = bytearray(make_cic6102_rom(size=0x4000))
        rom[self.AA_AT:self.AA_AT + 4] = core.AA_PATTERN
        applied, out = self._run(bytes(rom), no_dither=False)
        self.assertEqual(applied, {"NoAA"})
        self.assertEqual(core.detect_cic_chip(out), "6102")
        self.assertIsNotNone(core.calculate_n64_crc(out))

    def test_asset_data_past_the_code_region_is_not_touched(self):
        """A 4-byte match in compressed asset data is coincidence. The old
        patcher rewrote every hit in the whole ROM."""
        far = core.CODE_REGION_END + 0x40
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=far + 0x100))
        rom[far:far + 4] = core.AA_PATTERN
        rom = bytes(rom)
        applied, out = self._run(rom)
        self.assertEqual(applied, set())
        self.assertEqual(out[far:far + 4], core.AA_PATTERN)

    def test_unaligned_match_is_not_touched(self):
        """MIPS instructions are word-aligned; a hit at offset % 4 != 0 is
        data that happens to contain the byte sequence."""
        at = 0x1502  # deliberately not a multiple of 4
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x4000))
        rom[at:at + 4] = core.AA_PATTERN
        rom = bytes(rom)
        applied, out = self._run(rom)
        self.assertEqual(applied, set())
        self.assertEqual(out[at:at + 4], core.AA_PATTERN)

    def test_implausible_match_density_aborts(self):
        """Hundreds of hits means the scan is in data, not code. Bail out
        instead of rewriting all of it."""
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x8000))
        count = core.MAX_DYNAMIC_PATCH_SITES + 1
        for i in range(count):
            off = 0x1000 + i * 4
            rom[off:off + 4] = core.AA_PATTERN
        rom = bytes(rom)
        messages = []
        applied, out = self._run(rom, no_dither=False, log=messages.append)
        self.assertEqual(applied, set())
        self.assertEqual(out, rom)
        self.assertTrue(any("implausible" in m for m in messages), messages)

    def test_site_count_at_the_ceiling_still_patches(self):
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x8000))
        for i in range(core.MAX_DYNAMIC_PATCH_SITES):
            off = 0x1000 + i * 4
            rom[off:off + 4] = core.AA_PATTERN
        applied, out = self._run(bytes(rom), no_dither=False)
        self.assertEqual(applied, {"NoAA"})
        self.assertEqual(out.count(core.AA_PATTERN), 0)


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

    def test_build_output_path_keeps_moderately_long_names_intact(self):
        """Names that fit the filesystem limit are never shortened - the old
        65-char cap silently merged distinct ROMs onto one output name."""
        long_name = "A" * 100
        p = core.build_output_path(f"/roms/{long_name}.z64", {"NoAA"})
        self.assertEqual(os.path.basename(p), f"{long_name} [NoAA].z64")

    def test_build_output_path_long_name_fits_filesystem_limit(self):
        p = core.build_output_path("/roms/" + "A" * 400 + ".z64", {"NoAA"})
        self.assertLessEqual(
            len(os.path.basename(p).encode("utf-8")), core.MAX_FILENAME_BYTES)

    def test_build_output_path_long_shared_prefix_stays_distinct(self):
        """Two ROMs sharing a long prefix must not produce the same output
        name; truncation alone would collapse both onto one file."""
        prefix = "Some Very Long Homebrew Title " * 10
        a = core.build_output_path(f"/roms/{prefix}Disc 1.z64", {"NoAA"})
        b = core.build_output_path(f"/roms/{prefix}Disc 2.z64", {"NoAA"})
        self.assertNotEqual(a, b)


class TestOutputCollisions(unittest.TestCase):
    """build_output_path must never hand back a path that already exists -
    in a batch run shutil.move would overwrite the earlier result."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _touch(self, name, data=b"first"):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_existing_output_is_not_reused(self):
        src = self._touch("game.z64")
        existing = self._touch("game [NoAA].z64", b"earlier result")
        p = core.build_output_path(src, {"NoAA"})
        self.assertNotEqual(os.path.abspath(p), os.path.abspath(existing))
        self.assertEqual(os.path.basename(p), "game [NoAA] (2).z64")
        with open(existing, "rb") as f:
            self.assertEqual(f.read(), b"earlier result")

    def test_numbering_continues_past_two(self):
        src = self._touch("game.z64")
        self._touch("game [NoAA].z64")
        self._touch("game [NoAA] (2).z64")
        p = core.build_output_path(src, {"NoAA"})
        self.assertEqual(os.path.basename(p), "game [NoAA] (3).z64")

    def test_distinct_inputs_sharing_a_long_prefix_do_not_overwrite(self):
        """The data-loss case: long names truncated to the same stem."""
        prefix = "Long Title That Exceeds The Old Sixty Five Char Cap " * 2
        first = self._touch(f"{prefix}Alpha.z64")
        second = self._touch(f"{prefix}Beta.z64")
        p1 = core.build_output_path(first, {"NoAA"})
        with open(p1, "wb") as f:
            f.write(b"result one")
        p2 = core.build_output_path(second, {"NoAA"})
        self.assertNotEqual(os.path.abspath(p1), os.path.abspath(p2))
        with open(p1, "rb") as f:
            self.assertEqual(f.read(), b"result one")

    def test_reserve_output_path_is_exclusive(self):
        """Two concurrent workers reserving for the same input get two
        different names - the plain exists() check alone is racy."""
        src = self._touch("game.z64")
        a = core.reserve_output_path(src, {"NoAA"})
        b = core.reserve_output_path(src, {"NoAA"})
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_move_onto_reserved_replaces_placeholder(self):
        src = self._touch("game.z64")
        payload = self._touch("payload.bin", b"patched bytes")
        dst = core.reserve_output_path(src, {"NoAA"})
        core.move_onto_reserved(payload, dst)
        with open(dst, "rb") as f:
            self.assertEqual(f.read(), b"patched bytes")
        self.assertFalse(os.path.exists(payload))


class TestHiresSupportGate(unittest.TestCase):
    """Regression: the generic VI-table width flip shipped enabled for every
    ROM and renders incorrectly on hardware. Widening an OSViMode entry
    changes one field; the framebuffer the game allocated and the RDP
    coordinates it draws with still assume 320, so the image doubles and the
    UI lands in the wrong place. Confirmed on a SummerCart64. Hi-res is now
    offered only where a verified per-dump delta exists."""

    SM64 = (0x635A2BFF, 0x8B022326)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch_dir = os.path.join(self.tmp.name, "hires")
        os.makedirs(self.patch_dir)
        with open(os.path.join(self.patch_dir, "sm64.xdelta"), "wb") as f:
            f.write(bytes.fromhex("d6c3c400"))
        self.table = {self.SM64: ("sm64.xdelta", "Super Mario 64 (USA)")}

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    @staticmethod
    def _read(path):
        with open(path, "rb") as f:
            return f.read()

    def _rom(self, crc=(0xDEADBEEF, 0x12345678), tables=2, hires=False):
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x4000))
        rom[0x10:0x14] = crc[0].to_bytes(4, "big")
        rom[0x14:0x18] = crc[1].to_bytes(4, "big")
        width = core.WIDTH_640_DATA if hires else core.WIDTH_320_DATA
        for i in range(tables):
            off = 0x1000 + i * 0x40
            rom[off:off + 4] = width
            rom[off + 4:off + 8] = core.NTSC_BURST
        return bytes(rom)

    def _inspect(self, **kw):
        p = self._write("g.z64", self._rom(**kw))
        with mock.patch.object(core, "HIRES_PATCHES_DIR", self.patch_dir), \
             mock.patch.object(core, "SUBDRAG_PATCHES", self.table):
            return core.inspect_rom_details(p)

    # --- classification ---------------------------------------------------

    def test_verified_dump(self):
        info = self._inspect(crc=self.SM64)
        self.assertEqual(info["hires_support"], core.HIRES_VERIFIED)

    def test_unknown_dump_is_unsupported(self):
        info = self._inspect()
        self.assertEqual(info["hires_support"], core.HIRES_UNSUPPORTED)
        self.assertIn("framebuffer", info["hires_support_reason"])

    def test_native_hires_rom_needs_nothing(self):
        info = self._inspect(hires=True)
        self.assertEqual(info["hires_support"], core.HIRES_NATIVE)

    def test_rom_without_vi_tables_is_unsupported(self):
        info = self._inspect(tables=0)
        self.assertEqual(info["hires_support"], core.HIRES_UNSUPPORTED)

    # --- the gate itself --------------------------------------------------

    def test_unsupported_rom_is_not_widened(self):
        """The actual hardware bug: this used to rewrite the width words."""
        src = self._write("game.z64", self._rom())
        before = self._read(src)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        logs = []
        res = core.patch_rom(src, opts, log=logs.append)

        self.assertEqual(res["status"], "skipped", res)
        self.assertNotIn("HR", res["applied"])
        self.assertIn("NOT SUPPORTED", " ".join(logs))
        # Original untouched, and no widened output was produced.
        self.assertEqual(self._read(src), before)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if "[" in f], [])

    def test_force_hires_still_allows_it(self):
        src = self._write("game.z64", self._rom())
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)
        logs = []
        res = core.patch_rom(src, opts, log=logs.append)
        self.assertEqual(res["status"], "patched", res)
        self.assertIn("HR", res["applied"])
        joined = " ".join(logs)
        self.assertIn("EXPERIMENTAL", joined)
        self.assertIn("WARNING", joined)

    def test_native_hires_rom_is_left_alone(self):
        src = self._write("game.z64", self._rom(hires=True))
        before = self._read(src)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        core.patch_rom(src, opts, log=lambda m: None)
        self.assertEqual(self._read(src), before)

    def test_skip_reason_names_the_cause(self):
        src = self._write("game.z64", self._rom())
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        res = core.patch_rom(src, opts, log=lambda m: None)
        self.assertIn("640x480 not supported", res["message"])

    def _patch_verified_without_xdelta(self, **opt_kw):
        """Patch a dump that HAS a verified delta, on a machine where xdelta3
        cannot run - the normal situation on Linux and macOS, where the
        bundled helper is a Windows binary."""
        src = self._write("game.z64", self._rom(crc=self.SM64))
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 **opt_kw)
        logs = []
        with mock.patch.object(core, "HIRES_PATCHES_DIR", self.patch_dir), \
             mock.patch.object(core, "SUBDRAG_PATCHES", self.table), \
             mock.patch.object(core, "check_tools", lambda: {
                 "u64aap": False, "rn64crc": False, "xdelta3": False,
                 "hires_patches": True, "crc_native": True}):
            res = core.patch_rom(src, opts, log=logs.append)
        return src, res, logs

    def test_verified_dump_without_xdelta_is_not_widened(self):
        """A dump whose only correct route is its delta must not silently get
        the generic width flip when that route is closed. This is the same
        broken transform the hardware bug report was about, so falling back
        to it ships a ROM that renders doubled and misplaced."""
        src, res, logs = self._patch_verified_without_xdelta()
        joined = " ".join(logs)

        self.assertNotIn("HR", res["applied"])
        self.assertIn("NOT APPLIED", joined)
        self.assertIn("xdelta3", joined)
        # The width words in the VI tables are untouched.
        data = self._read(src)
        self.assertEqual(data[0x1000:0x1004], core.WIDTH_320_DATA)

    def test_missing_xdelta_names_the_install_command(self):
        """A user who cannot install what they are missing is stuck."""
        _src, _res, logs = self._patch_verified_without_xdelta()
        joined = " ".join(logs)
        self.assertIn(core.xdelta3_install_hint(), joined)

    def test_force_hires_overrides_the_missing_tool_gate(self):
        _src, res, logs = self._patch_verified_without_xdelta(force_hires=True)
        self.assertIn("HR", res["applied"])
        self.assertIn("EXPERIMENTAL", " ".join(logs))

    def test_verified_dump_is_not_blocked_when_xdelta_works(self):
        """The gate must not break the games that do work."""
        src = self._write("game.z64", self._rom(crc=self.SM64))
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        logs = []

        def fake_xdelta(patch_file, source, output):
            shutil.copyfile(source, output)
            return True, "SUCCESS"

        with mock.patch.object(core, "HIRES_PATCHES_DIR", self.patch_dir), \
             mock.patch.object(core, "SUBDRAG_PATCHES", self.table), \
             mock.patch.object(core, "check_tools", lambda: {
                 "u64aap": False, "rn64crc": False, "xdelta3": True,
                 "hires_patches": True, "crc_native": True}), \
             mock.patch.object(core, "try_subdrag_xdelta", fake_xdelta):
            res = core.patch_rom(src, opts, log=logs.append)
        self.assertIn("HR", res["applied"])
        self.assertNotIn("NOT SUPPORTED", " ".join(logs))
        self.assertNotIn("NOT APPLIED", " ".join(logs))

    def test_export_carries_support_columns(self):
        info = self._inspect()
        out = os.path.join(self.tmp.name, "r.csv")
        core.export_report([info], out)
        with open(out, encoding="utf-8") as f:
            header = f.readline()
        self.assertIn("hires_support", header)
        self.assertIn("hires_support_reason", header)


class TestPlatformPaths(unittest.TestCase):
    """The tool must write its log where each platform expects, and must
    never write next to the executable (read-only for installed bundles)."""

    def _log_dir(self, platform, env):
        with mock.patch.object(sys, "platform", platform), \
             mock.patch.dict(os.environ, env, clear=False):
            return core.get_log_dir()

    def test_windows_uses_appdata(self):
        got = self._log_dir("win32", {"APPDATA": os.path.join("X:", "Roaming")})
        self.assertTrue(got.endswith("N64SmartPatcher"))
        self.assertIn("Roaming", got)

    def test_macos_uses_library_logs(self):
        got = self._log_dir("darwin", {})
        self.assertIn(os.path.join("Library", "Logs"), got)

    def test_linux_honours_xdg_data_home(self):
        target = os.path.join(os.sep, "custom", "data")
        got = self._log_dir("linux", {"XDG_DATA_HOME": target})
        self.assertEqual(got, os.path.join(target, "n64-smart-patcher"))

    def test_linux_falls_back_when_xdg_is_unset_or_relative(self):
        """The XDG spec says a non-absolute value must be ignored."""
        for value in ("", "relative/path"):
            got = self._log_dir("linux", {"XDG_DATA_HOME": value})
            self.assertEqual(
                got,
                os.path.join(os.path.expanduser("~"), ".local", "share",
                             "n64-smart-patcher"),
                f"XDG_DATA_HOME={value!r}")

    def test_install_hint_is_platform_specific(self):
        hints = {}
        for plat in ("darwin", "linux", "win32"):
            with mock.patch.object(sys, "platform", plat):
                hints[plat] = core.xdelta3_install_hint()
        self.assertIn("brew", hints["darwin"])
        self.assertIn("apt", hints["linux"])
        # All three must differ, or the message is not actually helping.
        self.assertEqual(len(set(hints.values())), 3)


class TestSubdragCrcMatching(unittest.TestCase):
    """Deltas are keyed on the CRC1/CRC2 of the exact dump they were built
    against, each value derived by actually applying the delta. Title
    matching preceded this and was wrong twice: the keys "BANJO KAZOOIE"
    and "FORSAKEN 64" never matched the real internal titles
    "Banjo-Kazooie" and "Forsaken", so those two games silently never got
    their patch."""

    SM64 = (0x635A2BFF, 0x8B022326)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch_dir = os.path.join(self.tmp.name, "hires")
        os.makedirs(self.patch_dir)
        with open(os.path.join(self.patch_dir, "sm64.xdelta"), "wb") as f:
            f.write(bytes.fromhex("d6c3c400"))  # xdelta3 magic
        self.table = {self.SM64: ("sm64.xdelta", "Super Mario 64 (USA)")}

    def _lookup(self, crc1, crc2):
        with mock.patch.object(core, "HIRES_PATCHES_DIR", self.patch_dir),              mock.patch.object(core, "SUBDRAG_PATCHES", self.table):
            return core.get_subdrag_patch(crc1, crc2)

    def test_exact_dump_is_offered(self):
        self.assertIsNotNone(self._lookup(*self.SM64))

    def test_accepts_hex_strings_as_well_as_ints(self):
        """inspect_rom_details carries the checksums as hex text."""
        self.assertIsNotNone(self._lookup("635A2BFF", "8B022326"))

    def test_other_revision_is_not_offered(self):
        """The failure title matching could never catch: right game, wrong
        revision. Applying the delta could only fail."""
        self.assertIsNone(self._lookup(0x635A2BFF, 0xDEADBEEF))

    def test_unrelated_rom_is_not_offered(self):
        self.assertIsNone(self._lookup(0x11111111, 0x22222222))

    def test_missing_or_malformed_checksums_are_safe(self):
        for bad in (None, "", "Unknown", "zzzz"):
            self.assertIsNone(self._lookup(bad, bad), bad)

    def test_missing_patch_file_is_not_offered(self):
        os.remove(os.path.join(self.patch_dir, "sm64.xdelta"))
        self.assertIsNone(self._lookup(*self.SM64))

    def test_shipped_table_is_wellformed(self):
        """Guards the real table: 8 entries, int key pairs, no duplicate
        patch filenames."""
        self.assertEqual(len(core.SUBDRAG_PATCHES), 8)
        files = []
        for key, value in core.SUBDRAG_PATCHES.items():
            self.assertIsInstance(key, tuple)
            self.assertEqual(len(key), 2)
            self.assertTrue(all(isinstance(k, int) for k in key), key)
            self.assertEqual(len(value), 2)
            files.append(value[0])
        self.assertEqual(len(files), len(set(files)), "duplicate patch file")

    def test_inspect_reports_country_code(self):
        p = os.path.join(self.tmp.name, "pal.z64")
        with open(p, "wb") as f:
            f.write(make_synthetic_rom(country=b"P"))
        info = core.inspect_rom_details(p)
        self.assertEqual(info["country_code"], "P")

    def test_inspect_flags_a_known_dump(self):
        """has_subdrag_patch must key off the checksums, not the title."""
        rom = bytearray(make_synthetic_rom(vi_tables=0))
        rom[0x10:0x14] = (0x635A2BFF).to_bytes(4, "big")
        rom[0x14:0x18] = (0x8B022326).to_bytes(4, "big")
        p = os.path.join(self.tmp.name, "sm64.z64")
        with open(p, "wb") as f:
            f.write(bytes(rom))
        with mock.patch.object(core, "HIRES_PATCHES_DIR", self.patch_dir),              mock.patch.object(core, "SUBDRAG_PATCHES", self.table):
            self.assertTrue(core.inspect_rom_details(p)["has_subdrag_patch"])


class TestMixedResolutionReporting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _rom_with(self, n320, n640):
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x4000))
        off = 0x1000
        for _ in range(n320):
            rom[off:off + 4] = core.WIDTH_320_DATA
            rom[off + 4:off + 8] = core.NTSC_BURST
            off += 0x40
        for _ in range(n640):
            rom[off:off + 4] = core.WIDTH_640_DATA
            rom[off + 4:off + 8] = core.NTSC_BURST
            off += 0x40
        p = os.path.join(self.tmp.name, f"m{n320}_{n640}.z64")
        with open(p, "wb") as f:
            f.write(bytes(rom))
        return core.inspect_rom_details(p)

    def test_mixed_rom_is_flagged_and_not_called_hires(self):
        """is_hires_640x480 stays strict because it gates the 'already
        hi-res, skip' decision and a mixed ROM still has 320 tables to
        convert - but reports no longer have to call it plain 320x240."""
        info = self._rom_with(2, 3)
        self.assertFalse(info["is_hires_640x480"])
        self.assertTrue(info["is_mixed_resolution"])
        self.assertEqual(info["vi_table_count"], 2)
        self.assertEqual(info["vi_table_640_count"], 3)

    def test_fully_converted_rom(self):
        info = self._rom_with(0, 3)
        self.assertTrue(info["is_hires_640x480"])
        self.assertFalse(info["is_mixed_resolution"])
        self.assertEqual(info["vi_table_640_count"], 3)

    def test_untouched_rom(self):
        info = self._rom_with(3, 0)
        self.assertFalse(info["is_hires_640x480"])
        self.assertFalse(info["is_mixed_resolution"])


class TestAaDitherFlagsAreIndependent(unittest.TestCase):
    """Regression: no_aa was OR'd with no_dither, so a dither-only patched
    ROM reported as fully patched and patch_rom skipped it - AA never got
    removed and nothing said so."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _inspect(self, *patterns):
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x4000))
        off = 0x1400
        for pat in patterns:
            rom[off:off + 4] = pat
            off += 0x40
        p = os.path.join(self.tmp.name, f"f{len(patterns)}{off}.z64")
        with open(p, "wb") as f:
            f.write(bytes(rom))
        return core.inspect_rom_details(p)

    def test_dither_only_does_not_imply_no_aa(self):
        info = self._inspect(core.DITHER_REPLACEMENT)
        self.assertTrue(info["no_dither"])
        self.assertFalse(info["no_aa"])

    def test_aa_only(self):
        info = self._inspect(core.AA_REPLACEMENT)
        self.assertTrue(info["no_aa"])
        self.assertFalse(info["no_dither"])

    def test_both(self):
        info = self._inspect(core.AA_REPLACEMENT, core.DITHER_REPLACEMENT)
        self.assertTrue(info["no_aa"])
        self.assertTrue(info["no_dither"])

    def test_dither_only_rom_is_not_skipped_as_fully_patched(self):
        """A ROM whose dither is already done but whose AA is not: the OR
        made it look fully patched, so patch_rom skipped it and the AA
        site was left alone with no error shown."""
        rom = bytearray(make_synthetic_rom(vi_tables=1, size=0x4000))
        rom[0x1400:0x1404] = core.DITHER_REPLACEMENT  # dither already done
        rom[0x1440:0x1444] = core.AA_PATTERN          # AA still unpatched
        src = os.path.join(self.tmp.name, "dither_only.z64")
        with open(src, "wb") as f:
            f.write(bytes(rom))

        info = core.inspect_rom_details(src)
        self.assertTrue(info["no_dither"])
        self.assertFalse(info["no_aa"], "dither must not imply AA")

        opts = core.PatchOptions(no_aa=True, no_dither=False, hires=False)
        res = core.patch_rom(src, opts, log=lambda m: None)
        self.assertEqual(res["status"], "patched", res)
        with open(res["output"], "rb") as f:
            out = f.read()
        self.assertEqual(out[0x1440:0x1444], core.AA_REPLACEMENT)


class TestVerifyOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_valid_rom_passes(self):
        p = self._write("ok.z64", make_cic6102_rom())
        core.fix_rom_crc_native(p)
        verdict = core.verify_output(p)
        self.assertTrue(verdict["ok"], verdict)

    def test_wrong_checksums_fail_strictly(self):
        """The black-screen case: header CRCs that do not match the data."""
        rom = bytearray(make_cic6102_rom())
        rom[0x10:0x18] = b"\xDE\xAD\xBE\xEF\x12\x34\x56\x78"
        p = self._write("bad.z64", bytes(rom))
        verdict = core.verify_output(p)
        self.assertFalse(verdict["ok"])
        crc = next(c for c in verdict["checks"] if c["name"] == "crc")
        self.assertFalse(crc["ok"])
        self.assertTrue(crc["strict"])

    def test_unreadable_file_fails(self):
        verdict = core.verify_output(os.path.join(self.tmp.name, "nope.z64"))
        self.assertFalse(verdict["ok"])

    def test_non_rom_fails(self):
        p = self._write("junk.z64", b"\xFF" * 4096)
        self.assertFalse(core.verify_output(p)["ok"])

    def test_filter_expectations_are_advisory_not_fatal(self):
        """A NoAA applied through u64aap or a SubDrag delta need not leave
        the dynamic patcher's byte signature, so a missing signature must
        not fail an otherwise sound ROM."""
        p = self._write("ok.z64", make_cic6102_rom())
        core.fix_rom_crc_native(p)
        verdict = core.verify_output(p, applied={"NoAA", "NoDither"})
        self.assertTrue(verdict["ok"], verdict)
        advisory = [c for c in verdict["checks"] if not c["strict"]]
        self.assertTrue(advisory)
        self.assertFalse(all(c["ok"] for c in advisory))

    def test_verify_report_rows_carry_hashes_and_verdict(self):
        src = self._write("in.z64", make_cic6102_rom())
        out = self._write("out.z64", make_cic6102_rom())
        core.fix_rom_crc_native(out)
        rows = core.verify_report_rows(
            [{"input": src, "output": out, "applied": {"HR"}}])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["verified"])
        self.assertEqual(row["failed_checks"], "")
        self.assertEqual(len(row["input_md5"]), 32)
        self.assertEqual(len(row["input_sha1"]), 40)

    def test_verify_report_skips_results_without_output(self):
        rows = core.verify_report_rows([{"input": "x.z64", "output": None}])
        self.assertEqual(rows, [])


class TestCrcHeaderValidity(unittest.TestCase):
    """Regression: rn64crc.exe exits 0 even when it cannot identify the
    boot chip and leaves the header untouched, so patch_rom marked the CRC
    step done and never ran the native fallback - shipping a ROM that
    black-screens. The file, not the exit code, is the authority."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_detects_stale_checksums(self):
        p = self._write("stale.z64", make_cic6102_rom())  # header is DEADBEEF
        self.assertFalse(core.crc_header_is_valid(p))
        core.fix_rom_crc_native(p)
        self.assertTrue(core.crc_header_is_valid(p))

    def test_works_on_byte_swapped_images(self):
        p = self._write("s.v64", byteswap_halfwords(make_cic6102_rom()))
        self.assertFalse(core.crc_header_is_valid(p))
        core.fix_rom_crc_native(p)
        self.assertTrue(core.crc_header_is_valid(p))

    def test_unknown_cic_is_not_valid(self):
        p = self._write("nocic.z64", make_synthetic_rom(vi_tables=0))
        self.assertFalse(core.crc_header_is_valid(p))

    def test_pipeline_falls_back_when_external_tool_lies(self):
        """Simulate the real rn64crc behaviour: exit 0, change nothing."""
        rom = bytearray(make_cic6102_rom(size=0x4000))
        for i in range(2):
            off = 0x2000 + i * 0x40
            rom[off:off + 4] = core.WIDTH_320_DATA
            rom[off + 4:off + 8] = core.NTSC_BURST
        src = self._write("game.z64", bytes(rom))

        class FakeResult:
            returncode = 0
            stdout = "Unable to calculate!"
            stderr = ""

        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        logs = []
        fake_tools = {"rn64crc": True, "u64aap": False, "xdelta3": False,
                      "hires_patches": False, "crc_native": True}
        with mock.patch.object(core, "check_tools", lambda: fake_tools), \
             mock.patch.object(core, "_run_tool", lambda *a, **k: FakeResult()):
            res = core.patch_rom(src, opts, log=logs.append)

        self.assertEqual(res["status"], "patched", res)
        self.assertTrue(core.crc_header_is_valid(res["output"]),
                        "native engine did not run after the tool no-op")
        self.assertTrue(any("falling back to native" in m for m in logs), logs)
        self.assertTrue(core.verify_output(res["output"], res["applied"])["ok"])


class TestTempFilePlacement(unittest.TestCase):
    """Regression: working files were always created next to the input,
    which fails outright on a read-only source directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_prefers_output_dir(self):
        outdir = os.path.join(self.tmp.name, "out")
        os.makedirs(outdir)
        chosen = core._temp_dir_for(os.path.join(self.tmp.name, "rom.z64"), outdir)
        self.assertEqual(os.path.abspath(chosen), os.path.abspath(outdir))

    def test_falls_back_to_input_dir_when_no_output_dir(self):
        rom = os.path.join(self.tmp.name, "rom.z64")
        self.assertEqual(os.path.abspath(core._temp_dir_for(rom)),
                         os.path.abspath(self.tmp.name))

    def test_falls_back_to_system_temp_when_nothing_is_writable(self):
        rom = os.path.join(self.tmp.name, "rom.z64")
        with mock.patch("os.access", return_value=False):
            chosen = core._temp_dir_for(rom, os.path.join(self.tmp.name, "out"))
        self.assertEqual(os.path.abspath(chosen),
                         os.path.abspath(tempfile.gettempdir()))

    def test_read_only_source_dir_still_patches(self):
        """The end-to-end case: input directory not writable, output
        elsewhere. This used to fail at tempfile.mkstemp."""
        src_dir = os.path.join(self.tmp.name, "src")
        out_dir = os.path.join(self.tmp.name, "dst")
        os.makedirs(src_dir)
        os.makedirs(out_dir)
        src = os.path.join(src_dir, "game.z64")
        with open(src, "wb") as f:
            f.write(make_synthetic_rom(vi_tables=2))

        real_access = os.access

        def no_write_to_src(path, mode, *a, **kw):
            if mode == os.W_OK and os.path.abspath(path) == os.path.abspath(src_dir):
                return False
            return real_access(path, mode, *a, **kw)

        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        with mock.patch("os.access", no_write_to_src):
            res = core.patch_rom(src, opts, log=lambda m: None, output_dir=out_dir)
        self.assertEqual(res["status"], "patched", res)
        self.assertEqual(os.path.dirname(res["output"]), out_dir)
        # No working files left anywhere.
        self.assertEqual([f for f in os.listdir(src_dir) if ".temp" in f], [])
        self.assertEqual([f for f in os.listdir(out_dir) if ".temp" in f], [])


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
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
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
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        res = core.patch_rom(src, opts, log=lambda m: None)
        self.assertEqual(res["status"], "skipped")

    def test_cancel_aborts_run(self):
        rom = make_synthetic_rom(vi_tables=1)
        src = self._write("cancelme.z64", rom)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
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

        # Clean source ROM whose checksums match the mocked SubDrag entry
        # (lookup is by CRC1/CRC2, not by title).
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

        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        logs = []
        with mock.patch.object(core, "HIRES_PATCHES_DIR", fake_dir), \
             mock.patch.object(core, "SUBDRAG_PATCHES",
                               {(0xDEADBEEF, 0x12345678):
                                ("sm64 NoAA hires.xdelta", "SM64 fixture")}):
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


def reference_crc_6102(data):
    """Independent transcription of the published CRC routine for CIC-6102,
    kept deliberately close to the C original so it can arbitrate the
    boundary cases in core.calculate_n64_crc:

        if ((t6 + d) < t6) t4++;
        r = ROL(d, d & 0x1f);
        t6 += d; t3 ^= d; t5 += r;
        if (t2 > d) t2 ^= r; else t2 ^= t6 ^ d;
        t1 += d ^ t5;
    """
    mask = 0xFFFFFFFF
    seed = 0xF8CA4DDC
    t1 = t2 = t3 = t4 = t5 = t6 = seed

    def word(off):
        chunk = data[off:off + 4]
        if len(chunk) < 4:
            chunk = bytes(chunk) + b"\x00" * (4 - len(chunk))
        return int.from_bytes(chunk, "big")

    for i in range(0, 0x100000, 4):
        d = word(0x1000 + i)
        if ((t6 + d) & mask) < t6:
            t4 = (t4 + 1) & mask
        b = d & 0x1F
        r = ((d << b) | (d >> (32 - b))) & mask if b else d
        t6 = (t6 + d) & mask
        t3 ^= d
        t5 = (t5 + r) & mask
        if t2 > d:
            t2 ^= r
        else:
            t2 ^= t6 ^ d
        t1 = (t1 + (d ^ t5)) & mask

    return t6 ^ t4 ^ t3, t5 ^ t2 ^ t1


class TestCrcEngine(unittest.TestCase):
    """Pure-Python N64 boot CRC engine (emulator-compatible algorithm)."""

    def _make_cic6102_rom(self):
        return make_cic6102_rom()

    def test_matches_reference_implementation(self):
        rom = bytearray(make_cic6102_rom(size=0x8000))
        # Deterministic pseudo-random payload so the mixing is exercised.
        state = 0x13579BDF
        for off in range(0x1000, 0x8000, 4):
            state = (state * 1103515245 + 12345) & 0xFFFFFFFF
            rom[off:off + 4] = state.to_bytes(4, "big")
        self.assertEqual(core.calculate_n64_crc(bytes(rom), "6102"),
                         reference_crc_6102(bytes(rom)))

    def test_equal_accumulator_and_word_takes_sum_branch(self):
        """Regression: the reference compares `t2 > d`, so d == t2 must take
        the `t6 ^ d` branch. A `t2 < d` test sends equality the other way and
        stamps a wrong CRC - which is a black screen on hardware. The first
        data word is set to the CIC-6102 seed to hit it on iteration 0."""
        rom = bytearray(make_cic6102_rom())
        rom[0x1000:0x1004] = (0xF8CA4DDC).to_bytes(4, "big")  # == seed == a2
        rom = bytes(rom)
        self.assertEqual(core.calculate_n64_crc(rom, "6102"),
                         reference_crc_6102(rom))

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

    def _fix_swapped(self, swapped, ext, fmt):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, f"crc.{ext}")
            with open(p, "wb") as f:
                f.write(swapped)
            ok, msg = core.fix_rom_crc_native(p)
            self.assertTrue(ok, msg)
            with open(p, "rb") as f:
                out = f.read()
        # Still in its original byte order - fixing a checksum must not
        # silently convert the file's format.
        self.assertEqual(core.detect_format(out)[0], fmt)
        # ...and the stamped values are the ones the big-endian view needs.
        be = core.to_big_endian(out, fmt)
        expected = core.calculate_n64_crc(be)
        self.assertIsNotNone(expected)
        self.assertEqual(be[0x10:0x14], expected[0].to_bytes(4, "big"))
        self.assertEqual(be[0x14:0x18], expected[1].to_bytes(4, "big"))
        return out

    def test_fix_native_handles_v64(self):
        """Regression: --fix-crc on a byte-swapped dump used to fail with
        'Not a big-endian .z64 image' instead of just converting."""
        original = byteswap_halfwords(self._make_cic6102_rom())
        out = self._fix_swapped(original, "v64", "v64")
        self.assertEqual(out[:0x10], original[:0x10])   # only 0x10..0x18 changed
        self.assertEqual(out[0x18:], original[0x18:])

    def test_fix_native_handles_n64(self):
        self._fix_swapped(byteswap_words(self._make_cic6102_rom()), "n64", "n64")

    def test_fix_native_rejects_non_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "junk.z64")
            with open(p, "wb") as f:
                f.write(b"\xFF" * 4096)
            ok, msg = core.fix_rom_crc_native(p)
            self.assertFalse(ok)
            self.assertIn("recognizable", msg)

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
            opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
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
