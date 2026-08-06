"""Unit tests for ips_bps_patcher.

Includes a minimal reference BPS *encoder* implementing byuu's spec so
patches can be generated inside the tests (no external fixtures).
"""
import os
import struct
import tempfile
import unittest
import zlib

from ips_bps_patcher import (apply_ips_patch, apply_bps_patch,
                             detect_patch_type, _bps_read_vlv)


# ---------------------------------------------------------------- helpers

def bps_encode_vlv(n: int) -> bytes:
    """Encode one variable-length value (inverse of _bps_read_vlv)."""
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            break
        out.append(x)
        n -= 1
    return bytes(out)


def bps_command(length: int, command: int) -> bytes:
    return bps_encode_vlv(((length - 1) << 2) | command)


def bps_copy_offset(magnitude: int, negative: bool) -> bytes:
    return bps_encode_vlv((magnitude << 1) | (1 if negative else 0))


def make_bps(source: bytes, commands: bytes, declared_target: bytes = None,
             metadata: bytes = b"") -> bytes:
    """Assemble a complete BPS patch. When declared_target is given its
    CRC32 is written to the footer (allows building corrupt-target tests);
    otherwise the commands are assumed to reproduce declared_target."""
    body = (b"BPS1" + bps_encode_vlv(len(source)) +
            bps_encode_vlv(len(declared_target) if declared_target is not None else 0) +
            bps_encode_vlv(len(metadata)) + metadata + commands)
    footer_head = struct.pack("<II", zlib.crc32(source) & 0xFFFFFFFF,
                              zlib.crc32(declared_target) & 0xFFFFFFFF
                              if declared_target is not None else 0)
    patch_crc = zlib.crc32(body + footer_head) & 0xFFFFFFFF
    return body + footer_head + struct.pack("<I", patch_crc)


class WriteHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p


class TestVlvCodec(WriteHelper):
    def test_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 383, 16383, 16384, 100000, 2**21):
            encoded = bps_encode_vlv(value)
            decoded, pos = _bps_read_vlv(encoded, 0)
            self.assertEqual(decoded, value, f"roundtrip failed for {value}")
            self.assertEqual(pos, len(encoded))

    def test_multibyte_case_from_old_bug(self):
        # The pre-3.1 decoder returned 255 instead of 383 here.
        encoded = bytes([0x7F, 0x81])
        decoded, _ = _bps_read_vlv(encoded, 0)
        self.assertEqual(decoded, 383)


class TestDetectPatchType(WriteHelper):
    def test_by_magic(self):
        self.assertEqual(detect_patch_type(self._write("a.bin", b"PATCH" + b"\x00" * 8)), "ips")
        self.assertEqual(detect_patch_type(self._write("b.bin", b"BPS1" + b"\x00" * 8)), "bps")
        self.assertEqual(detect_patch_type(self._write("c.bin", b"UPS1" + b"\x00" * 8)), "ups")
        self.assertEqual(detect_patch_type(self._write("d.bin", b"JUNKJUNK")), "unknown")

    def test_by_extension(self):
        p = self._write("y.bps", b"\x00\x00\x00\x00")
        self.assertEqual(detect_patch_type(p), "bps")
        p2 = self._write("z.ips", b"")
        self.assertEqual(detect_patch_type(p2), "ips")


class TestIpsPatcher(WriteHelper):
    def test_normal_and_rle_records(self):
        rom = bytes(range(256)) * 4  # 1024 bytes
        patch = (b"PATCH"
                 + b"\x00\x00\x10" + b"\x00\x05" + b"HELLO"      # normal @0x10
                 + b"\x00\x01\x00" + b"\x00\x00"                  # RLE marker @0x100
                 + b"\x00\x0A" + b"\xAA"                          # 10 x 0xAA
                 + b"EOF")
        src = self._write("rom.bin", rom)
        pst = self._write("patch.ips", patch)
        out = os.path.join(self.tmp.name, "out.bin")
        res = apply_ips_patch(src, pst, out)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            patched = f.read()
        self.assertEqual(len(patched), 1024)
        self.assertEqual(patched[0x10:0x15], b"HELLO")
        self.assertEqual(patched[0x100:0x10A], b"\xAA" * 10)
        self.assertEqual(patched[0x10A], rom[0x10A])  # untouched after RLE

    def test_expansion_and_truncation(self):
        rom = b"\x00" * 16
        # write at 0x20 (beyond EOF -> expands), then truncate to 0x24
        patch = (b"PATCH"
                 + b"\x00\x00\x20" + b"\x00\x04" + b"DATA"
                 + b"EOF" + b"\x00\x00\x24")
        src = self._write("rom.bin", rom)
        pst = self._write("patch.ips", patch)
        out = os.path.join(self.tmp.name, "out.bin")
        res = apply_ips_patch(src, pst, out)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            patched = f.read()
        self.assertEqual(len(patched), 0x24)
        self.assertEqual(patched[0x20:0x24], b"DATA")

    def test_invalid_header(self):
        src = self._write("rom.bin", b"\x00" * 16)
        pst = self._write("bad.ips", b"NOPES" + b"\x00" * 4)
        res = apply_ips_patch(src, pst, os.path.join(self.tmp.name, "o"))
        self.assertEqual(res["status"], "error")
        self.assertIn("Invalid IPS", res["message"])

    def test_truncated_patch(self):
        src = self._write("rom.bin", b"\x00" * 16)
        pst = self._write("trunc.ips", b"PATCH" + b"\x00\x00\x00" + b"\x00\x10")  # claims 16 bytes, none follow
        res = apply_ips_patch(src, pst, os.path.join(self.tmp.name, "o"))
        self.assertEqual(res["status"], "error")


class TestBpsPatcher(WriteHelper):
    def test_target_read_only_large(self):
        # Full-target TargetRead forces a multi-byte VLV length (> 127),
        # exercising the fixed decoder.
        source = bytes(range(256))
        target = bytearray(source)
        target[0x10:0x15] = b"HELLO"
        target = bytes(target)
        commands = bps_command(len(target), 0) + target
        patch = make_bps(source, commands, declared_target=target)
        src = self._write("rom.bin", source)
        pst = self._write("patch.bps", patch)
        out = os.path.join(self.tmp.name, "out.bin")
        res = apply_bps_patch(src, pst, out)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), target)

    def test_source_read_and_copies(self):
        source = b"AABBCCDD" * 8  # 64 bytes
        literal = b"XYZXYZXY"
        target = (source[0:8]        # SourceRead
                  + source[16:24]    # SourceCopy +16
                  + source[8:16]     # SourceCopy -16
                  + literal          # TargetRead
                  + source[0:8])     # TargetCopy back to start
        commands = (bps_command(8, 1)
                    + bps_command(8, 2) + bps_copy_offset(16, negative=False)
                    + bps_command(8, 2) + bps_copy_offset(16, negative=True)
                    + bps_command(8, 0) + literal
                    + bps_command(8, 3) + bps_copy_offset(0, negative=False))
        patch = make_bps(source, commands, declared_target=target)
        src = self._write("rom.bin", source)
        pst = self._write("patch.bps", patch)
        out = os.path.join(self.tmp.name, "out.bin")
        res = apply_bps_patch(src, pst, out)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), target)

    def test_target_copy_overlap_rle(self):
        # TargetCopy reading into itself produces run-length encoding.
        source = b""  # empty source: everything via TargetRead/TargetCopy
        target = b"Q" + b"Q" * 15  # 16 x 'Q'
        commands = (bps_command(1, 0) + b"Q"
                    + bps_command(15, 3) + bps_copy_offset(0, negative=False))
        patch = make_bps(source, commands, declared_target=target)
        src = self._write("rom.bin", source)
        pst = self._write("patch.bps", patch)
        out = os.path.join(self.tmp.name, "out.bin")
        res = apply_bps_patch(src, pst, out)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), target)

    def test_metadata_skipped(self):
        source = b"abcdef"
        target = b"abcXef"
        meta = b"somefile.txt\x00"
        commands = bps_command(3, 1) + bps_command(1, 0) + b"X" + bps_command(2, 1)
        patch = make_bps(source, commands, declared_target=target, metadata=meta)
        src = self._write("rom.bin", source)
        pst = self._write("patch.bps", patch)
        out = os.path.join(self.tmp.name, "out.bin")
        res = apply_bps_patch(src, pst, out)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), target)

    def test_wrong_source_rejected(self):
        source = b"the right rom data...."
        target = b"THE RIGHT ROM DATA...."
        commands = bps_command(len(target), 0) + target
        patch = make_bps(source, commands, declared_target=target)
        src = self._write("wrong.bin", b"a completely different rom")
        pst = self._write("patch.bps", patch)
        res = apply_bps_patch(src, pst, os.path.join(self.tmp.name, "o"))
        self.assertEqual(res["status"], "error")
        self.assertIn("source CRC32 mismatch", res["message"])

    def test_corrupt_patch_rejected(self):
        source = b"0123456789"
        target = b"0123XXXX89"
        commands = (bps_command(4, 1) + bps_command(4, 0) + b"XXXX"
                    + bps_command(2, 1))
        patch = bytearray(make_bps(source, commands, declared_target=target))
        patch[8] ^= 0xFF  # corrupt one body byte -> patch CRC must fail
        src = self._write("rom.bin", source)
        pst = self._write("patch.bps", bytes(patch))
        res = apply_bps_patch(src, pst, os.path.join(self.tmp.name, "o"))
        self.assertEqual(res["status"], "error")
        self.assertIn("patch CRC32 mismatch", res["message"])

    def test_target_crc_mismatch_rejected(self):
        source = b"XXXXXXXXXXXXXXXX"
        declared_but_wrong = b"Z" * 16
        commands = bps_command(16, 1)
        # Footer gets the WRONG target CRC, but the patch CRC is computed
        # over the assembled body, so only the target check can fail.
        patch = make_bps(source, commands, declared_target=declared_but_wrong)
        src = self._write("rom.bin", source)
        pst = self._write("patch.bps", patch)
        res = apply_bps_patch(src, pst, os.path.join(self.tmp.name, "o"))
        self.assertEqual(res["status"], "error")
        self.assertIn("target CRC32 mismatch", res["message"])

    def test_invalid_header(self):
        src = self._write("rom.bin", b"\x00" * 16)
        pst = self._write("bad.bps", b"NOPE" + b"\x00" * 32)
        res = apply_bps_patch(src, pst, os.path.join(self.tmp.name, "o"))
        self.assertEqual(res["status"], "error")
        self.assertIn("Invalid BPS", res["message"])


if __name__ == "__main__":
    unittest.main()
