"""The shipped SM64 menu fix: structure, lookup, and effect.

The fix is data, not code, so what is tested is the contract around it:
the IPS touches exactly the four hardware-confirmed menu emitters and
nothing else, the Stage 1b lookup finds it for the right CRC1, and
applying it to a buffer that carries plausible original instructions
produces exactly the doubled-coordinate / halved-texture-step words the
analysis describes.
"""

import importlib.util
import os
import struct
import unittest

from n64patcher import ips_bps_patcher
from n64patcher import n64_core as core

_SM64_CRC1 = "635A2BFF"
_MENU_SITE_INDEXES = (1, 2, 3, 4)


def _load_makefix():
    path = os.path.join(os.path.dirname(core.__file__), "..", "..",
                        "scripts", "sm64_hires", "makefix.py")
    path = os.path.normpath(path)
    spec = importlib.util.spec_from_file_location("sm64_makefix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_ips(data):
    """[(offset, bytes)] for a plain (non-RLE) IPS."""
    assert data[:5] == b"PATCH", "missing IPS header"
    assert data[-3:] == b"EOF", "missing IPS trailer"
    records, pos = [], 5
    while data[pos:pos + 3] != b"EOF":
        offset = int.from_bytes(data[pos:pos + 3], "big")
        size = int.from_bytes(data[pos + 3:pos + 5], "big")
        pos += 5
        assert size > 0, "RLE records are not expected here"
        records.append((offset, data[pos:pos + size]))
        pos += size
    return records


def _site_word_ranges(makefix):
    ranges = []
    for index in _MENU_SITE_INDEXES:
        _emitter, _note, coords, dsdx, dtdy = makefix.SITES[index]
        for off in [*coords, dsdx, dtdy]:
            ranges.append(range(off, off + 4))
    return ranges


class TestShippedMenuFix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.makefix = _load_makefix()
        # A user-level fix (someone's hardware-test build) legitimately
        # overrides the shipped one; the lookup is tested against the
        # shipped directory alone so it stays hermetic.
        cls._orig_dirs = core.game_fix_dirs
        core.game_fix_dirs = lambda: [core.GAME_FIXES_DIR]
        cls.fix_path = core.get_game_fix_for_rom(_SM64_CRC1)
        with open(cls.fix_path, "rb") as f:
            cls.records = _parse_ips(f.read())

    @classmethod
    def tearDownClass(cls):
        core.game_fix_dirs = cls._orig_dirs

    def test_the_lookup_finds_the_shipped_file(self):
        self.assertIsNotNone(self.fix_path)
        name = os.path.basename(self.fix_path)
        self.assertTrue(name.startswith(_SM64_CRC1), name)
        self.assertTrue(name.endswith(".ips"), name)
        # int and str spellings key the same lookup
        self.assertEqual(
            core.get_game_fix_for_rom(0x635A2BFF), self.fix_path)

    def test_every_changed_byte_lies_inside_the_four_menu_sites(self):
        allowed = set()
        for rng in _site_word_ranges(self.makefix):
            allowed.update(rng)
        for offset, data in self.records:
            for i in range(len(data)):
                self.assertIn(offset + i, allowed,
                              f"byte {offset + i:08X} is outside sites 1-4")

    def test_every_site_word_has_its_transform_byte_changed(self):
        """Each word's low half carries the edit (shamt bit / immediate),
        so at least one byte per word must appear in the IPS."""
        changed = set()
        for offset, data in self.records:
            changed.update(range(offset, offset + len(data)))
        for rng in _site_word_ranges(self.makefix):
            self.assertTrue(set(rng) & changed,
                            f"word {rng.start:08X} is not touched at all")
        # 4 sites x (4 coordinate words + 2 texture-step words) = 24 words
        self.assertEqual(len(_site_word_ranges(self.makefix)), 24)

    def test_applying_it_doubles_the_coordinates_and_halves_the_steps(self):
        # `sll $2, $2, 2` for every coordinate word; lui with immediate
        # 0x0400 for the texture steps (the hi-res image's real steps are
        # 0x04xx, so halving is exact). The IPS overwrites only the low
        # half of each word - the fields the transforms touch - and the
        # untouched synthetic bytes must survive it.
        coords, steps = [], []
        for index in _MENU_SITE_INDEXES:
            _e, _n, c, d, t = self.makefix.SITES[index]
            coords.extend(c)
            steps.extend((d, t))
        size = max(coords + steps) + 0x100
        buffer = bytearray(b"\x00" * size)
        for off in coords:
            struct.pack_into(">I", buffer, off, 0x00021080)  # sll $2,$2,2
        for off in steps:
            struct.pack_into(">I", buffer, off, 0x3C040400)  # lui $4, 0x400

        src = "menu_fix_source.bin"
        dst = "menu_fix_patched.bin"
        with open(src, "wb") as f:
            f.write(bytes(buffer))
        self.addCleanup(os.remove, src)
        self.addCleanup(lambda: os.path.exists(dst) and os.remove(dst))

        res = ips_bps_patcher.apply_ips_patch(
            src, self.fix_path, dst, require_n64=False)
        self.assertEqual(res["status"], "patched", res.get("message"))
        with open(dst, "rb") as f:
            out = f.read()

        for off in coords:
            word = struct.unpack_from(">I", out, off)[0]
            self.assertEqual(word, 0x000210C0,
                             f"{off:08X}: shamt not shifted to 3")
        for off in steps:
            word = struct.unpack_from(">I", out, off)[0]
            self.assertEqual(word, 0x3C040200,
                             f"{off:08X}: immediate not halved "
                             f"(got {word & 0xFFFF:04X})")

    def test_shipped_bytes_are_the_verified_wip_restricted_to_menus(self):
        """Two independent artifacts, one claim: the shipped IPS must be
        exactly the hardware-verified .wip's changed bytes for sites 1-4."""
        base = os.path.dirname(self.makefix.__file__)
        spec = importlib.util.spec_from_file_location(
            "sm64_make_ips", os.path.join(base, "make_ips.py"))
        make_ips = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make_ips)

        with open(os.path.join(base, make_ips.WIP_NAME), "rb") as f:
            patch = f.read()
        literals, _src, _dst = make_ips.parse_bps_literals(patch)
        changed = {}
        for off, data in literals:
            for i, byte in enumerate(data):
                changed[off + i] = byte
        expected = make_ips.selected_bytes(changed, list(_MENU_SITE_INDEXES))

        shipped = {}
        for offset, data in self.records:
            for i, byte in enumerate(data):
                shipped[offset + i] = byte
        self.assertEqual(shipped, expected)

    def test_apply_game_fix_reports_success_for_the_keyed_crc(self):
        import tempfile
        orig_dirs = core.game_fix_dirs
        core.game_fix_dirs = lambda: [core.GAME_FIXES_DIR]
        self.addCleanup(setattr, core, "game_fix_dirs", orig_dirs)
        with tempfile.TemporaryDirectory() as tmp:
            rom = os.path.join(tmp, "sm64_hires.z64")
            size = max(off for off in
                       [r[0] + len(r[1]) for r in self.records]) + 0x100
            buffer = bytearray(b"\x00" * size)
            # Enough of a header that the format check recognises z64.
            buffer[0:4] = b"\x80\x37\x12\x40"
            with open(rom, "wb") as f:
                f.write(bytes(buffer))
            out = rom + ".fixed.z64"
            ok, message = core.apply_game_fix(rom, _SM64_CRC1, out)
            self.assertTrue(ok, message)
            self.assertIn("sm64_menu_2x", message)
            self.assertGreater(os.path.getsize(out), 0)


if __name__ == "__main__":
    unittest.main()
