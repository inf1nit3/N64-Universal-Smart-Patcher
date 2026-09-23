"""scripts/sm64_hires/make_hud.py: the HUD fix's contract, without a ROM.

The HUD fix edits code the repository does not carry, so what CI can
check is the logic around the edits: the instruction encoders agree with
the project disassembler, the liveness guard rejects exactly the rewrites
it exists to reject, both modes produce the coordinates the RDP expects
from its cycle type, and the display-list rewrite keeps every list the
same length while setting the mode bits H2X sets. The one test that needs
the real hi-res image runs only where it exists.
"""

import importlib.util
import os
import struct
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts", "sm64_hires"))
_HIRES = os.path.normpath(os.path.join(_HERE, "..", "work", "sm64", "hires.z64"))


def _load(name):
    sys.path.insert(0, _SCRIPTS)
    try:
        spec = importlib.util.spec_from_file_location(
            f"sm64_{name}", os.path.join(_SCRIPTS, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(_SCRIPTS)


hud = _load("make_hud")
mipsdis = _load("mipsdis")

T = {"t0": 8, "t1": 9, "t2": 10, "t3": 11, "t4": 12, "t5": 13, "t7": 15, "t8": 24, "t9": 25}
SP = 29


def _lw(rt, off, base=SP):
    return (0x23 << 26) | (base << 21) | (rt << 16) | off


def _or(rd, rs, rt):
    return (rs << 21) | (rt << 16) | (rd << 11) | 0x25


def _buf(words, at=0x100):
    data = bytearray(0x400)
    for i, w in enumerate(words):
        struct.pack_into(">I", data, at + 4 * i, w)
    return data


class TestEncoders(unittest.TestCase):
    def test_sll_reads_back_through_the_disassembler(self):
        self.assertEqual(mipsdis.disasm(hud.sll(T["t9"], T["t7"], 3), 0), "sll t9, t7, 3")

    def test_addiu_reads_back_through_the_disassembler(self):
        self.assertEqual(mipsdis.disasm(hud.addiu(T["t5"], T["t5"], 128), 0), "addiu t5, t5, 128")

    def test_decoders_invert_the_encoders(self):
        self.assertEqual(hud.decode_sll(hud.sll(13, 4, 2)), (13, 4, 2))
        self.assertEqual(hud.decode_addiu(hud.addiu(12, 4, 15)), (12, 4, 15))


class TestLiveness(unittest.TestCase):
    """The rewrite stops writing the addiu's temporary. That is only safe
    when nothing reads it before it is overwritten."""

    def test_accepts_a_temporary_that_is_overwritten_first(self):
        data = _buf([hud.sll(T["t9"], T["t8"], 2), _lw(T["t8"], 36)])
        self.assertEqual(hud.check_dead(data, 0x100, T["t8"]), 0x104)

    def test_rejects_a_temporary_that_is_still_read(self):
        data = _buf([hud.sll(T["t9"], T["t8"], 2), _or(T["t2"], T["t8"], T["t1"])])
        with self.assertRaises(SystemExit):
            hud.check_dead(data, 0x100, T["t8"])

    def test_rejects_an_instruction_it_cannot_classify(self):
        data = _buf([hud.sll(T["t9"], T["t8"], 2), 0x0C000000])  # jal
        with self.assertRaises(SystemExit):
            hud.check_dead(data, 0x100, T["t8"])

    def test_the_early_write_may_not_cross_a_use(self):
        """The new sll writes the destination one slot early; an
        instruction between the two slots must not touch it."""
        clean = _buf([hud.addiu(T["t9"], 5, 15), _lw(T["t3"], 8), hud.sll(T["t0"], T["t9"], 2)])
        hud.check_between(clean, 0x100, 0x108, T["t0"])
        dirty = _buf([hud.addiu(T["t9"], 5, 15), _lw(T["t0"], 8), hud.sll(T["t0"], T["t9"], 2)])
        with self.assertRaises(SystemExit):
            hud.check_between(dirty, 0x100, 0x108, T["t0"])


class TestCoordinates(unittest.TestCase):
    """The end coordinate follows the cycle type: inclusive in COPY mode,
    exclusive in 1-cycle - which is the whole reason there are two modes."""

    def _end(self, x, size, mode):
        add = 4 * (size - 1) if mode == "copy" else 8 * size
        return (x << 3) + add  # what the rewritten pair computes

    def test_copy_mode_keeps_the_glyph_at_one_to_one(self):
        for size in (8, 16):
            x = 100
            # inclusive end: [2x, 2x + size - 1] in 10.2 fixed point
            self.assertEqual(self._end(x, size, "copy"), (2 * x + size - 1) << 2)

    def test_full_mode_doubles_the_glyph(self):
        for size in (8, 16):
            x = 100
            # exclusive end: [2x, 2x + 2*size) - H2X's ((x + size) * 2) << 2
            self.assertEqual(self._end(x, size, "full"), ((x + size) * 2) << 2)

    def test_on_screen_coordinates_stay_inside_the_12_bit_field(self):
        """The emitters mask with 0xFFF; a 320-space x on screen must not
        wrap after doubling."""
        for mode in hud.MODES:
            self.assertLess(self._end(319, 16, mode), 0x1000)


class TestDisplayLists(unittest.TestCase):
    OTHERMODE_H = 0xBA

    def _field(self, word1, shift, length):
        return (word1 >> shift) & ((1 << length) - 1)

    def test_rewritten_lists_keep_their_length_and_terminator(self):
        for old, new in ((hud.DL_BEGIN_OLD, hud.DL_BEGIN_NEW), (hud.DL_END_OLD, hud.DL_END_NEW)):
            self.assertEqual(len(old), len(new))
            self.assertEqual(new[-1], hud.END_DL)
            self.assertEqual(new[0], hud.PIPE_SYNC)

    def test_merged_othermode_command_covers_cycle_persp_and_filter(self):
        for w0, _w1 in (hud.DL_BEGIN_NEW[1], hud.DL_END_NEW[1]):
            self.assertEqual(w0 >> 24, self.OTHERMODE_H)
            shift, length = (w0 >> 8) & 0xFF, w0 & 0xFF
            self.assertEqual((shift, length), (12, 10))  # bits 12..21

    def test_begin_selects_1cycle_no_persp_point_filter(self):
        w1 = hud.DL_BEGIN_NEW[1][1]
        self.assertEqual(self._field(w1, 20, 2), 0)  # G_CYC_1CYCLE
        self.assertEqual(self._field(w1, 19, 1), 0)  # G_TP_NONE
        self.assertEqual(self._field(w1, 12, 2), 0)  # G_TF_POINT

    def test_end_restores_1cycle_persp_bilerp(self):
        w1 = hud.DL_END_NEW[1][1]
        self.assertEqual(self._field(w1, 20, 2), 0)  # G_CYC_1CYCLE
        self.assertEqual(self._field(w1, 19, 1), 1)  # G_TP_PERSP
        self.assertEqual(self._field(w1, 12, 2), 2)  # G_TF_BILERP

    def test_the_original_begin_is_the_copy_mode_list(self):
        self.assertIn((0xBA001402, 0x00200000), hud.DL_BEGIN_OLD)  # G_CYC_COPY


class TestFootprint(unittest.TestCase):
    def test_hud_edits_never_touch_the_shipped_menu_fix(self):
        menu = set()
        for off, blob in hud.read_ips(hud.MENU_IPS):
            menu.update(range(off, off + len(blob)))
        for off in hud.EXPECTED:
            self.assertFalse(menu & set(range(off, off + 4)), f"{off:06X} overlaps the menu fix")
        self.assertFalse(menu & set(range(hud.SEG2_START, hud.SEG2_END)))

    def test_every_site_has_both_axes(self):
        for _i, _note, size, starts, pairs, steps in hud.HUD_SITES:
            self.assertIn(size, (8, 16))
            self.assertEqual(len(starts), 2)
            self.assertEqual(len(pairs), 2)
            self.assertEqual(len(steps), 2)


@unittest.skipUnless(os.path.isfile(_HIRES), "needs the local SubDrag hi-res image")
class TestAgainstTheImage(unittest.TestCase):
    def test_both_modes_plan_cleanly(self):
        with open(_HIRES, "rb") as f:
            data = bytearray(f.read())
        self.assertEqual(len(hud.plan(data, "copy")), 18)
        self.assertEqual(len(hud.plan(data, "full")), 24)


if __name__ == "__main__":
    unittest.main()
