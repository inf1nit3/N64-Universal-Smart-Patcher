"""Unit tests for the hires2d toolkit's transforms.

The tools run against real hi-res images that cannot be in the
repository; what CAN be tested is the arithmetic the tools apply - the
s10.2 shift doubling, the sign-aware s5.10 halving, and the IPS
emission - on synthetic buffers with known words.
"""

import importlib.util
import os
import struct
import unittest


def _load(name):
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "hires2d", name)
    spec = importlib.util.spec_from_file_location(f"hires2d_{name[:-3]}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


makefix2d = _load("makefix2d.py")
find2d = _load("find2d.py")


def sll(rd: int, rt: int, shamt: int) -> int:
    return (rt << 16) | (rd << 11) | (shamt << 6)


def ori(rt: int, rs: int, imm: int) -> int:
    return (0x0D << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


class TestShiftDoubling(unittest.TestCase):
    def test_sll_two_becomes_sll_three(self):
        buffer = bytearray(8)
        struct.pack_into(">I", buffer, 0, sll(2, 3, 2))
        new = makefix2d.double_shift(buffer, 0)
        self.assertEqual(new, sll(2, 3, 3))
        # only the shamt bit moved; registers survive
        self.assertEqual((new >> 11) & 0x1F, 2)
        self.assertEqual((new >> 16) & 0x1F, 3)

    def test_anything_else_is_refused(self):
        buffer = bytearray(8)
        for word in (sll(2, 3, 1), sll(2, 3, 4), ori(1, 2, 4), 0):
            struct.pack_into(">I", buffer, 0, word)
            with self.assertRaises(SystemExit):
                makefix2d.double_shift(buffer, 0)


class TestStepHalving(unittest.TestCase):
    def check(self, imm, expected):
        buffer = bytearray(8)
        struct.pack_into(">I", buffer, 0, ori(4, 4, imm))
        new = makefix2d.halve_step_imm(buffer, 0)
        self.assertEqual(new & 0xFFFF, expected)

    def test_positive_steps_halve(self):
        self.check(0x0400, 0x0200)  # s5.10 1.0 -> 0.5
        self.check(0x0800, 0x0400)  # 2.0 -> 1.0
        self.check(0x0200, 0x0100)

    def test_negative_steps_halve_sign_aware(self):
        self.check(0xFC00, 0xFE00)  # -1.0 -> -0.5
        self.check(0xF800, 0xFC00)  # -2.0 -> -1.0

    def test_odd_steps_are_refused(self):
        buffer = bytearray(8)
        struct.pack_into(">I", buffer, 0, ori(4, 4, 0x0401))
        with self.assertRaises(SystemExit):
            makefix2d.halve_step_imm(buffer, 0)

    def test_non_ori_lui_is_refused(self):
        buffer = bytearray(8)
        struct.pack_into(">I", buffer, 0, sll(2, 3, 2))
        with self.assertRaises(SystemExit):
            makefix2d.halve_step_imm(buffer, 0)

    def test_lui_high_half_halves(self):
        buffer = bytearray(8)
        struct.pack_into(">I", buffer, 0, (0x0F << 26) | 0x0400)
        new = makefix2d.halve_step_imm(buffer, 0)
        self.assertEqual(new & 0xFFFF, 0x0200)


class TestIpsEmission(unittest.TestCase):
    def test_records_and_eof(self):
        edits = {
            0x100: struct.pack(">I", 0xAABBCCDD),
            0x104: struct.pack(">I", 0x11223344),
            0x200: struct.pack(">I", 0xDEADBEEF),
        }
        ips = makefix2d.build_ips(edits)
        self.assertTrue(ips.startswith(b"PATCH"))
        self.assertTrue(ips.endswith(b"EOF"))
        # adjacent offsets merge into one record
        self.assertEqual(ips[5:8], bytes([0x00, 0x01, 0x00]))  # offset 0x100
        self.assertEqual(ips[8:10], b"\x00\x08")  # merged length 8
        self.assertEqual(ips[10:14], b"\xaa\xbb\xcc\xdd")
        self.assertEqual(ips[14:18], b"\x11\x22\x33\x44")
        # then the separated word at 0x200
        self.assertEqual(ips[18:21], bytes([0x00, 0x02, 0x00]))
        self.assertEqual(ips[21:23], b"\x00\x04")
        self.assertEqual(ips[23:27], b"\xde\xad\xbe\xef")


class TestPackerFinder(unittest.TestCase):
    def test_signature_and_noise(self):
        buffer = bytearray(64)
        struct.pack_into(">I", buffer, 0x10, sll(9, 8, 2))
        andi = (0x0C << 26) | (9 << 21) | (9 << 16) | 0x0FFF
        struct.pack_into(">I", buffer, 0x18, andi)
        # a shift whose consumer never appears
        struct.pack_into(">I", buffer, 0x30, sll(4, 5, 2))
        hits = find2d.find_shift_packers(bytes(buffer))
        self.assertEqual([off for off, _w, _u in hits], [0x10])


if __name__ == "__main__":
    unittest.main()
