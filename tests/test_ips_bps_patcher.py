"""Unit tests for ips_bps_patcher.

Includes a minimal reference BPS *encoder* implementing byuu's spec so
patches can be generated inside the tests (no external fixtures).
"""
import os
import struct
import tempfile
import unittest
import zlib

from n64patcher.ips_bps_patcher import (
    BPS_SOURCE_COPY,
    BPS_SOURCE_READ,
    BPS_TARGET_COPY,
    BPS_TARGET_READ,
    _bps_read_vlv,
    apply_bps_patch,
    apply_ips_patch,
    create_bps_patch,
    detect_patch_type,
)

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
    """Encode one BPS action. *command* must be one of the BPS_* constants
    imported from the module under test - hardcoding the numbers here is
    what previously let the encoder and the applier agree on a swapped
    convention that no real patch uses."""
    return bps_encode_vlv(((length - 1) << 2) | command)


def bps_copy_offset(magnitude: int, negative: bool) -> bytes:
    return bps_encode_vlv((magnitude << 1) | (1 if negative else 0))


def make_bps(source: bytes, commands: bytes, declared_target: bytes | None = None,
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
        res = apply_ips_patch(src, pst, out, require_n64=False)
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
        res = apply_ips_patch(src, pst, out, require_n64=False)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            patched = f.read()
        self.assertEqual(len(patched), 0x24)
        self.assertEqual(patched[0x20:0x24], b"DATA")

    def test_invalid_header(self):
        src = self._write("rom.bin", b"\x00" * 16)
        pst = self._write("bad.ips", b"NOPES" + b"\x00" * 4)
        res = apply_ips_patch(src, pst, os.path.join(self.tmp.name, "o"),
                              require_n64=False)
        self.assertEqual(res["status"], "error")
        self.assertIn("Invalid IPS", res["message"])

    def test_truncated_patch(self):
        src = self._write("rom.bin", b"\x00" * 16)
        pst = self._write("trunc.ips", b"PATCH" + b"\x00\x00\x00" + b"\x00\x10")  # claims 16 bytes, none follow
        res = apply_ips_patch(src, pst, os.path.join(self.tmp.name, "o"),
                              require_n64=False)
        self.assertEqual(res["status"], "error")


class TestIpsRomFormatGuard(WriteHelper):
    """IPS carries no checksum, so a wrong-byte-order source produces
    garbage and reports success. BPS is only safe here because its CRC32
    gate happens to catch it."""

    PATCH = b"PATCH" + b"\x00\x10\x00" + b"\x00\x04" + b"WXYZ" + b"EOF"

    def _z64(self, size=0x2000):
        rom = bytearray(size)
        rom[0:4] = bytes.fromhex("80371240")
        rom[32:52] = b"TEST GAME".ljust(20, b" ")
        return bytes(rom)

    def _apply(self, rom_bytes, name="rom.z64"):
        src = self._write(name, rom_bytes)
        pst = self._write("p.ips", self.PATCH)
        out = os.path.join(self.tmp.name, "out.z64")
        return apply_ips_patch(src, pst, pst and out), out

    def test_z64_applies_unchanged(self):
        res, out = self._apply(self._z64())
        self.assertEqual(res["status"], "patched", res)
        self.assertEqual(res["warnings"], [])
        with open(out, "rb") as f:
            self.assertEqual(f.read()[0x1000:0x1004], b"WXYZ")

    def test_v64_is_converted_not_corrupted(self):
        """The regression: a byte-swapped dump used to be patched raw."""
        z64 = self._z64()
        swapped = bytearray(z64)
        swapped[0::2], swapped[1::2] = bytes(swapped[1::2]), bytes(swapped[0::2])
        res, out = self._apply(bytes(swapped), "rom.v64")
        self.assertEqual(res["status"], "patched", res)
        self.assertIn("converted .v64", res["message"])
        with open(out, "rb") as f:
            patched = f.read()
        # Output is native big-endian and the record landed at its offset.
        self.assertEqual(patched[:4], bytes.fromhex("80371240"))
        self.assertEqual(patched[0x1000:0x1004], b"WXYZ")

    def test_non_rom_is_rejected(self):
        res, _ = self._apply(b"\xFF" * 0x2000)
        self.assertEqual(res["status"], "error")
        self.assertIn("Not a recognizable N64 ROM", res["message"])

    def test_oversized_rom_warns_about_3_byte_offsets(self):
        from n64patcher.ips_bps_patcher import IPS_MAX_ADDRESSABLE
        res, _ = self._apply(self._z64(IPS_MAX_ADDRESSABLE + 0x2000))
        self.assertEqual(res["status"], "patched", res)
        self.assertTrue(any("3 bytes" in w for w in res["warnings"]), res)


class TestBpsPatcher(WriteHelper):
    def test_target_read_only_large(self):
        # Full-target TargetRead forces a multi-byte VLV length (> 127),
        # exercising the fixed decoder.
        source = bytes(range(256))
        target = bytearray(source)
        target[0x10:0x15] = b"HELLO"
        target = bytes(target)
        commands = bps_command(len(target), BPS_TARGET_READ) + target
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
        commands = (bps_command(8, BPS_SOURCE_READ)
                    + bps_command(8, BPS_SOURCE_COPY) + bps_copy_offset(16, negative=False)
                    + bps_command(8, BPS_SOURCE_COPY) + bps_copy_offset(16, negative=True)
                    + bps_command(8, BPS_TARGET_READ) + literal
                    + bps_command(8, BPS_TARGET_COPY) + bps_copy_offset(0, negative=False))
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
        commands = (bps_command(1, BPS_TARGET_READ) + b"Q"
                    + bps_command(15, BPS_TARGET_COPY) + bps_copy_offset(0, negative=False))
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
        commands = bps_command(3, BPS_SOURCE_READ) + bps_command(1, BPS_TARGET_READ) + b"X" + bps_command(2, BPS_SOURCE_READ)
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
        commands = bps_command(len(target), BPS_TARGET_READ) + target
        patch = make_bps(source, commands, declared_target=target)
        src = self._write("wrong.bin", b"a completely different rom")
        pst = self._write("patch.bps", patch)
        res = apply_bps_patch(src, pst, os.path.join(self.tmp.name, "o"))
        self.assertEqual(res["status"], "error")
        self.assertIn("source CRC32 mismatch", res["message"])

    def test_corrupt_patch_rejected(self):
        source = b"0123456789"
        target = b"0123XXXX89"
        commands = (bps_command(4, BPS_SOURCE_READ) + bps_command(4, BPS_TARGET_READ) + b"XXXX"
                    + bps_command(2, BPS_SOURCE_READ))
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
        commands = bps_command(16, BPS_SOURCE_READ)
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


class TestBpsSpecConformance(WriteHelper):
    """Regression: the applier had SourceRead and TargetRead swapped
    (0 and 1). Our own tests passed because the test encoder used the same
    swapped convention - a closed loop that agreed with itself and with no
    real patch. Every genuine .bps failed its target CRC32 check.

    These build patches from the spec text directly, without going through
    the shared encoder, so the two cannot drift together again.
    """

    def test_action_constants_match_the_spec(self):
        self.assertEqual(
            (BPS_SOURCE_READ, BPS_TARGET_READ, BPS_SOURCE_COPY, BPS_TARGET_COPY),
            (0, 1, 2, 3))

    def _independent_patch(self, source, target, edit_at, literal):
        """Encode a patch using only the spec's numbering, inline."""
        def vlv(n):
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

        body = bytearray(b"BPS1") + vlv(len(source)) + vlv(len(target)) + vlv(0)
        body += vlv(((edit_at - 1) << 2) | 0)                    # SourceRead
        body += vlv(((len(literal) - 1) << 2) | 1) + literal     # TargetRead
        tail = len(target) - edit_at - len(literal)
        body += vlv(((tail - 1) << 2) | 0)                       # SourceRead
        body += struct.pack("<I", zlib.crc32(source) & 0xFFFFFFFF)
        body += struct.pack("<I", zlib.crc32(target) & 0xFFFFFFFF)
        body += struct.pack("<I", zlib.crc32(bytes(body)) & 0xFFFFFFFF)
        return bytes(body)

    def test_applies_a_patch_from_an_independent_encoder(self):
        source = bytes(range(256)) * 8
        target = bytearray(source)
        target[0x100:0x104] = b"HACK"
        target = bytes(target)

        src = self._write("s.bin", source)
        pst = self._write("p.bps", self._independent_patch(
            source, target, 0x100, b"HACK"))
        out = os.path.join(self.tmp.name, "o.bin")
        res = apply_bps_patch(src, pst, out)
        self.assertEqual(res["status"], "patched", res)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), target)


class TestBpsCreation(WriteHelper):
    """The differ. Appliers are common; without this the tool could consume
    community patches but never produce one."""

    def _roundtrip(self, source, target):
        sp = self._write("s.bin", source)
        tp = self._write("t.bin", target)
        pp = os.path.join(self.tmp.name, "p.bps")
        op = os.path.join(self.tmp.name, "o.bin")
        created = create_bps_patch(sp, tp, pp)
        self.assertEqual(created["status"], "created", created)
        applied = apply_bps_patch(sp, pp, op)
        self.assertEqual(applied["status"], "patched", applied)
        with open(op, "rb") as f:
            self.assertEqual(f.read(), target)
        return created

    def test_single_edit(self):
        source = bytes(range(256)) * 4
        target = bytearray(source)
        target[100:104] = b"WXYZ"
        self._roundtrip(source, bytes(target))

    def test_scattered_edits(self):
        source = bytes(range(256)) * 8
        target = bytearray(source)
        for off in (0x10, 0x200, 0x555, 0x7F0):
            target[off] ^= 0xFF
        self._roundtrip(source, bytes(target))

    def test_target_longer_than_source(self):
        source = b"A" * 512
        self._roundtrip(source, source + b"B" * 128)

    def test_target_shorter_than_source(self):
        source = b"A" * 512 + b"B" * 128
        self._roundtrip(source, b"A" * 512)

    def test_completely_different(self):
        self._roundtrip(b"\x00" * 300, b"\xFF" * 300)

    def test_patch_carries_correct_checksums(self):
        source = bytes(range(256))
        target = bytearray(source)
        target[0] = 0xFF
        created = self._roundtrip(source, bytes(target))
        with open(created["output"], "rb") as f:
            patch = f.read()
        src_crc, tgt_crc, patch_crc = struct.unpack("<III", patch[-12:])
        self.assertEqual(src_crc, zlib.crc32(source) & 0xFFFFFFFF)
        self.assertEqual(tgt_crc, zlib.crc32(bytes(target)) & 0xFFFFFFFF)
        self.assertEqual(patch_crc, zlib.crc32(patch[:-4]) & 0xFFFFFFFF)

    def test_patch_is_far_smaller_than_the_rom(self):
        """A one-word edit must not produce a patch the size of the ROM."""
        source = bytes(0x4000)
        target = bytearray(source)
        target[0x2000:0x2004] = b"EDIT"
        created = self._roundtrip(source, bytes(target))
        self.assertLess(created["size"], 100, "SourceRead runs are not being used")

    def test_identical_files_refused(self):
        data = bytes(range(256))
        sp = self._write("s.bin", data)
        tp = self._write("t.bin", data)
        res = create_bps_patch(sp, tp, os.path.join(self.tmp.name, "p.bps"))
        self.assertEqual(res["status"], "error")
        self.assertIn("identical", res["message"])

    def test_missing_input_reported(self):
        res = create_bps_patch(os.path.join(self.tmp.name, "nope"),
                               self._write("t.bin", b"x"),
                               os.path.join(self.tmp.name, "p.bps"))
        self.assertEqual(res["status"], "error")

    def test_created_patch_is_rejected_for_a_different_source(self):
        """The CRC32 gate must still fire on patches we produced."""
        source = bytes(range(256))
        target = bytearray(source)
        target[5] = 0x99
        sp = self._write("s.bin", source)
        tp = self._write("t.bin", bytes(target))
        pp = os.path.join(self.tmp.name, "p.bps")
        create_bps_patch(sp, tp, pp)
        wrong = self._write("wrong.bin", bytes(range(256))[::-1])
        res = apply_bps_patch(wrong, pp, os.path.join(self.tmp.name, "o.bin"))
        self.assertEqual(res["status"], "error")
        self.assertIn("source CRC32", res["message"])
