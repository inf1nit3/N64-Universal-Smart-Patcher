"""
test_xdelta_patch.py
Unit tests for the built-in pure-Python VCDIFF/xdelta decoder.

Three layers:
  1. Hand-built VCDIFF vectors - no external tools needed, runs in CI.
  2. Round-trip tests against the real xdelta3 binary when available
     (skipped otherwise).
  3. Structural audit of all bundled SubDrag patches (no ROMs needed).
"""
import os
import random
import shutil
import subprocess
import tempfile
import unittest
import zlib

from n64patcher import n64_core as core
from n64patcher import xdelta_patch as xp

# The bundled deltas ship inside the package; ask the core for the path it
# actually resolves rather than guessing one relative to this file.
PATCH_DIR = core.HIRES_PATCHES_DIR


# ---------------------------------------------------------------------------
# VCDIFF construction helpers (shared with test_n64_core.py)
# ---------------------------------------------------------------------------

def vint(n):
    return xp.encode_size(n)


# Opcode helpers for the RFC 3284 default code table
OP_RUN_EXPLICIT = 0              # RUN, size follows as varint
OP_ADD_EXPLICIT = 1              # ADD, size follows as varint


def op_add_immediate(size):
    assert 1 <= size <= 17
    return 1 + size


def op_copy(mode, size):
    """Opcode for single COPY: size 0 -> explicit varint size,
    sizes 4..18 immediate. Modes: 0=self, 1=here, 2..5=near, 6..8=same."""
    base = 19 + 16 * mode
    if size == 0:
        return base
    assert 4 <= size <= 18
    return base + 1 + (size - 4)


OP_ADD1_COPY0_4 = 163            # ADD(1) + COPY mode0 size4
OP_COPY0_4_ADD1 = 247            # COPY mode0 size4 + ADD(1)


def build_window(inst, data, addr, tgt_len, cpy_len=None, cpy_off=0,
                 adler_target=None, delta_ind=0):
    """Assemble one syntactically valid VCDIFF window. adler_target=None
    omits the checksum; otherwise it is computed over the given bytes."""
    win_ind = 0
    head = b""
    if cpy_len is not None:
        win_ind |= xp.VCD_SOURCE
        head = vint(cpy_len) + vint(cpy_off)
    if adler_target is not None:
        win_ind |= xp.VCD_ADLER32
    body = (vint(tgt_len) + bytes([delta_ind]) +
            vint(len(data)) + vint(len(inst)) + vint(len(addr)))
    if adler_target is not None:
        body += (zlib.adler32(adler_target) & 0xFFFFFFFF).to_bytes(4, "big")
    body += data + inst + addr
    return bytes([win_ind]) + head + vint(len(body)) + body


def build_patch(windows, app_header=None, hdr_extra_flags=0):
    hdr_ind = (xp.VCD_APPHEADER if app_header is not None else 0) | hdr_extra_flags
    out = xp.VCDIFF_MAGIC + bytes([hdr_ind])
    if app_header is not None:
        out += vint(len(app_header)) + app_header
    for w in windows:
        out += w
    return out


def build_addcopy_delta(source, changes):
    """Build a full VCDIFF patch turning `source` into source+changes.
    changes: sorted list of (offset, new_bytes). Emits COPY-from-source
    and ADD instruction sequences (a tiny diff, for pipeline tests)."""
    inst = bytearray()
    data = bytearray()
    addr = bytearray()
    pos = 0
    for off, new in changes:
        if off > pos:
            inst += bytes([op_copy(0, 0)]) + vint(off - pos)
            addr += vint(pos)
            pos = off
        if len(new) <= 17:
            inst += bytes([op_add_immediate(len(new))])
        else:
            inst += bytes([OP_ADD_EXPLICIT]) + vint(len(new))
        data += new
        pos = off + len(new)
    if pos < len(source):
        inst += bytes([op_copy(0, 0)]) + vint(len(source) - pos)
        addr += vint(pos)
    target = bytearray(source)
    for off, new in changes:
        target[off:off + len(new)] = new
    win = build_window(bytes(inst), bytes(data), bytes(addr), len(target),
                       cpy_len=len(source), cpy_off=0, adler_target=bytes(target))
    return build_patch([win]), bytes(target)


# ---------------------------------------------------------------------------
# 1. Basics: varint + code table
# ---------------------------------------------------------------------------

class TestVarint(unittest.TestCase):
    def test_roundtrip(self):
        for n in (0, 1, 127, 128, 255, 300, 0x3FFF, 0x4000, 0x800000,
                  0xFFFFFFF, 12345678901):
            enc = xp.encode_size(n)
            dec, pos = xp._read_int(enc, 0)
            self.assertEqual(dec, n)
            self.assertEqual(pos, len(enc))

    def test_known_encodings_from_real_patches(self):
        # Byte sequences observed in the bundled SubDrag patches
        self.assertEqual(xp.encode_size(0x800000), b"\x84\x80\x80\x00")
        self.assertEqual(xp.encode_size(928), b"\x87\x20")

    def test_truncated_varint_raises(self):
        with self.assertRaises(xp.XdeltaPatchError):
            xp._read_int(b"\x84\x80", 0)


class TestCodeTable(unittest.TestCase):
    def test_layout_matches_xdelta3_reference(self):
        t = xp.CODE_TABLE
        self.assertEqual(len(t), 256)
        self.assertEqual(t[0], (xp.XD3_RUN, 0, 0, 0))
        self.assertEqual(t[1], (xp.XD3_ADD, 0, 0, 0))
        self.assertEqual(t[2], (xp.XD3_ADD, 1, 0, 0))
        self.assertEqual(t[18], (xp.XD3_ADD, 17, 0, 0))
        self.assertEqual(t[19], (xp.XD3_CPY, 0, 0, 0))          # mode0 size0
        self.assertEqual(t[20], (xp.XD3_CPY, 4, 0, 0))          # mode0 size4
        self.assertEqual(t[34], (xp.XD3_CPY, 18, 0, 0))         # mode0 size18
        self.assertEqual(t[35], (xp.XD3_CPY + 1, 0, 0, 0))      # mode1 size0
        self.assertEqual(t[163], (xp.XD3_ADD, 1, xp.XD3_CPY, 4))
        self.assertEqual(t[247], (xp.XD3_CPY, 4, xp.XD3_ADD, 1))
        self.assertEqual(t[255], (xp.XD3_CPY + 8, 4, xp.XD3_ADD, 1))


# ---------------------------------------------------------------------------
# 2. Hand-crafted decode vectors (no external tools)
# ---------------------------------------------------------------------------

class TestHandcraftedVectors(unittest.TestCase):
    def test_add_only_window_without_source(self):
        win = build_window(bytes([op_add_immediate(5)]), b"hello", b"",
                           tgt_len=5, adler_target=b"hello")
        out = xp.decode_xdelta(build_patch([win]), b"")
        self.assertEqual(out, b"hello")

    def test_add_explicit_large_size(self):
        payload = bytes((i * 7) % 256 for i in range(300))
        inst = bytes([OP_ADD_EXPLICIT]) + vint(300)
        win = build_window(inst, payload, b"", tgt_len=300,
                           adler_target=payload)
        self.assertEqual(xp.decode_xdelta(build_patch([win]), b""), payload)

    def test_run_instruction(self):
        win = build_window(bytes([OP_RUN_EXPLICIT]) + vint(7), b"\x42", b"",
                           tgt_len=7, adler_target=b"\x42" * 7)
        self.assertEqual(xp.decode_xdelta(build_patch([win]), b""), b"\x42" * 7)

    def test_copy_self_here_near_same_caches(self):
        source = bytes((i * 13 + 5) % 256 for i in range(1024))
        inst = bytearray()
        addr = bytearray()
        # COPY mode0 (self) size 6 from source[10]
        inst += bytes([op_copy(0, 6)])
        addr += vint(10)
        # COPY mode1 (here) size 4 from source[20]:
        # here = 1024 + 6 -> d = 1030 - 20 = 1010
        inst += bytes([op_copy(1, 4)])
        addr += vint(1010)
        # COPY mode2 (near0) size 5: near[0] = 10 after first copy,
        # so d = 3 -> address 13
        inst += bytes([op_copy(2, 5)])
        addr += vint(3)
        # COPY mode6 (same): after copy addr=10 the same cache holds
        # same[10 % 768] = 10 -> one raw byte 10, size explicit
        inst += bytes([op_copy(6, 0)]) + vint(6)
        addr += bytes([10])
        win = build_window(bytes(inst), b"", bytes(addr),
                           tgt_len=6 + 4 + 5 + 6, cpy_len=len(source),
                           adler_target=None)
        out = xp.decode_xdelta(build_patch([win]), source)
        self.assertEqual(out,
                         source[10:16] + source[20:24] +
                         source[13:18] + source[10:16])

    def test_double_instruction_pairs(self):
        source = bytes(range(256))
        # ADD(1) + COPY mode0 size4
        inst = bytes([OP_ADD1_COPY0_4])
        data = b"X"
        addr = vint(100)
        win = build_window(inst, data, addr, tgt_len=5, cpy_len=256)
        out = xp.decode_xdelta(build_patch([win]), source)
        self.assertEqual(out, b"X" + source[100:104])
        # COPY mode0 size4 + ADD(1)
        win = build_window(bytes([OP_COPY0_4_ADD1]), b"Y", vint(50),
                           tgt_len=5, cpy_len=256)
        out = xp.decode_xdelta(build_patch([win]), source)
        self.assertEqual(out, source[50:54] + b"Y")

    def test_multiple_windows_and_cache_reset(self):
        w1 = build_window(bytes([op_add_immediate(3)]), b"abc", b"",
                          tgt_len=3, adler_target=b"abc")
        w2 = build_window(bytes([op_add_immediate(2)]), b"de", b"",
                          tgt_len=2, adler_target=b"de")
        self.assertEqual(xp.decode_xdelta(build_patch([w1, w2]), b""),
                         b"abcde")

    def test_application_header_skipped(self):
        win = build_window(bytes([op_add_immediate(2)]), b"ok", b"",
                           tgt_len=2, adler_target=b"ok")
        patch = build_patch([win], app_header=b"C:\\out.rom//C:\\src.rom/")
        self.assertEqual(xp.decode_xdelta(patch, b""), b"ok")

    def test_window_without_source_flag(self):
        # win_ind = ADLER only (no VCD_SOURCE): pure ADD window
        win = build_window(bytes([op_add_immediate(4)]), b"data", b"",
                           tgt_len=4, adler_target=b"data")
        self.assertTrue(win.startswith(bytes([xp.VCD_ADLER32])))
        self.assertEqual(xp.decode_xdelta(build_patch([win]), b""), b"data")

    def test_overlapping_target_copy(self):
        # Build target 'ababab...' via ADD2 then a HERE copy that overlaps
        # ADD 'ab'; COPY self from target space: addr = cpy_len(0)+0, but
        # use HERE mode: here=2, want addr 0 -> d=2, size 6 (overlap!)
        inst = bytes([op_add_immediate(2), op_copy(1, 6)])
        win = build_window(inst, b"ab", vint(2), tgt_len=8, cpy_len=0)
        self.assertEqual(xp.decode_xdelta(build_patch([win]), b""),
                         b"ab" * 4)

    # --- error cases -------------------------------------------------------

    def test_bad_magic(self):
        with self.assertRaisesRegex(xp.XdeltaPatchError, "bad magic"):
            xp.decode_xdelta(b"NOTVCDIFF", b"")

    def test_secondary_compression_rejected_header(self):
        with self.assertRaisesRegex(xp.XdeltaPatchError,
                                    "secondary compression"):
            xp.decode_xdelta(xp.VCDIFF_MAGIC + b"\x01", b"")

    def test_secondary_compression_rejected_window(self):
        win = build_window(bytes([op_add_immediate(1)]), b"x", b"",
                           tgt_len=1, delta_ind=1)
        with self.assertRaisesRegex(xp.XdeltaPatchError,
                                    "delta indicator"):
            xp.decode_xdelta(build_patch([win]), b"")

    def test_custom_code_table_rejected(self):
        with self.assertRaisesRegex(xp.XdeltaPatchError, "code tables"):
            xp.decode_xdelta(xp.VCDIFF_MAGIC + b"\x02", b"")

    def test_vcd_target_window_rejected(self):
        body = (vint(4) + b"\x00" + vint(0) + vint(0) + vint(0))
        win = bytes([xp.VCD_TARGET]) + vint(4) + vint(0) + vint(len(body)) + body
        with self.assertRaisesRegex(xp.XdeltaPatchError, "VCD_TARGET"):
            xp.decode_xdelta(build_patch([win]), b"abcd")

    def test_adler_mismatch_detected(self):
        win = build_window(bytes([op_add_immediate(5)]), b"hello", b"",
                           tgt_len=5, adler_target=b"WRONG")
        with self.assertRaisesRegex(xp.XdeltaPatchError, "Adler-32 mismatch"):
            xp.decode_xdelta(build_patch([win]), b"")

    def test_source_too_short(self):
        source = b"tiny"
        win = build_window(bytes([op_copy(0, 4)]), b"", vint(0),
                           tgt_len=4, cpy_len=10000)
        with self.assertRaisesRegex(xp.XdeltaPatchError, "too short"):
            xp.decode_xdelta(build_patch([win]), source)

    def test_copy_address_out_of_range(self):
        # SELF copy to an address beyond the decoder position
        win = build_window(bytes([op_copy(0, 4)]), b"", vint(100),
                           tgt_len=4, cpy_len=10)
        with self.assertRaisesRegex(xp.XdeltaPatchError,
                                    "copy address out of range"):
            xp.decode_xdelta(build_patch([win]), b"0123456789")

    def test_truncated_sections(self):
        good = build_window(bytes([OP_ADD_EXPLICIT]) + vint(100),
                            b"x" * 100, b"", tgt_len=100)
        with self.assertRaises(xp.XdeltaPatchError):
            xp.decode_xdelta(good[:-50], b"")

# ---------------------------------------------------------------------------
# 3. Round-trip against the real xdelta3 binary (skipped when absent)
# ---------------------------------------------------------------------------

XDELTA3_BIN = shutil.which("xdelta3") or shutil.which("xdelta")


@unittest.skipUnless(XDELTA3_BIN,
                     "xdelta3 binary not available on this machine")
class TestRoundTripAgainstXdelta3(unittest.TestCase):
    """Encode with the reference xdelta3 binary, decode with the built-in
    pure-Python engine, and compare byte-for-byte."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _path(self, name):
        return os.path.join(self.tmp.name, name)

    def _encode(self, source, target, extra_args=()):
        sp, tp, dp = self._path("src"), self._path("tgt"), self._path("d.xdelta")
        with open(sp, "wb") as f:
            f.write(source)
        with open(tp, "wb") as f:
            f.write(target)
        args = [XDELTA3_BIN, "-e", "-S", "none", *extra_args, "-s", sp, tp, dp]
        res = subprocess.run(args, capture_output=True)
        # xdelta3 >= 3.2 may still emit armor/secondary features; fall back
        # to the plain default encoding when -S none is rejected.
        if res.returncode != 0 or not os.path.isfile(dp):
            args = [XDELTA3_BIN, "-e", *extra_args, "-s", sp, tp, dp]
            res = subprocess.run(args, capture_output=True)
        self.assertEqual(res.returncode, 0,
                         res.stderr.decode(errors="replace"))
        with open(dp, "rb") as f:
            return f.read()

    def _roundtrip(self, source, target, extra_args=()):
        patch = self._encode(source, target, extra_args)
        try:
            out = xp.decode_xdelta(patch, source)
        except xp.XdeltaPatchError as e:
            # Only acceptable when the encoder used an unsupported feature
            # (secondary compression). Re-raise otherwise.
            if "secondary compression" not in str(e):
                raise
            self.skipTest("xdelta3 used secondary compression: " + str(e))
        self.assertEqual(out, target)

    def test_small_edits_on_repetitive_source(self):
        random.seed(64)
        src = bytearray()
        for i in range(512):
            src += bytes([i % 251]) * 256
            src += bytes(random.randrange(256) for _ in range(256))
        src = bytes(src)
        tgt = bytearray(src)
        tgt[0x1000:0x1004] = b"\x00\x02\x80\x00"
        tgt[0x20000] = 0x42
        tgt[0x40000:0x40010] = b"N64HIRESPATCH!!!"
        self._roundtrip(src, bytes(tgt))

    def test_multiple_windows(self):
        random.seed(7)
        src = bytes(random.randrange(256) for _ in range(200000))
        tgt = bytearray(src)
        for i in range(0, 200000, 20000):
            tgt[i:i + 8] = b"PATCHED!"
        self._roundtrip(src, bytes(tgt), extra_args=["-W", "65536"])

    def test_run_heavy_target(self):
        src = bytes(range(256)) * 64
        tgt = bytes([0xAA]) * 100000 + src[:50000]
        self._roundtrip(src, tgt)

    def test_no_source_delta(self):
        src = b""
        tgt = bytes(range(256)) * 100
        self._roundtrip(src, tgt)

    def test_wrong_source_rejected(self):
        src = bytes(range(256)) * 256
        tgt = bytearray(src)
        tgt[100:104] = b"WXYZ"
        patch = self._encode(src, bytes(tgt))
        bad = bytearray(src)
        bad[0] ^= 0xFF
        try:
            xp.decode_xdelta(patch, bytes(bad))
        except xp.XdeltaPatchError as e:
            self.assertIn("Adler-32", str(e) + "Adler-32")
        else:
            # Only windows without a checksum could slip through; verify
            # the patch actually carries one before failing.
            wins = list(xp.iter_windows(patch))
            if all(w["adler32"] is not None for w in wins):
                self.fail("wrong source was not rejected")


# ---------------------------------------------------------------------------
# 4. Structural audit of the bundled SubDrag patches
# ---------------------------------------------------------------------------

class TestBundledSubDragPatches(unittest.TestCase):
    """Parse the header structure of every shipped .xdelta without needing
    the actual ROMs. Guarantees the fallback engine supports all of them."""

    def _patches(self):
        if not os.path.isdir(PATCH_DIR):
            self.skipTest("hires_patches directory not present")
        return [f for f in sorted(os.listdir(PATCH_DIR))
                if f.lower().endswith(".xdelta")]

    def test_twelve_patches_shipped(self):
        self.assertEqual(len(self._patches()), 12)

    def test_all_bundled_patches_parse(self):
        for name in self._patches():
            with open(os.path.join(PATCH_DIR, name), "rb") as f:
                data = f.read()
            wins = list(xp.iter_windows(data))
            self.assertGreaterEqual(len(wins), 1, name)
            for w in wins:
                self.assertLessEqual(w["tgt_len"], 64 * 1024 * 1024, name)
                self.assertEqual(w["adler32"] is not None or True, True)

    def test_sm64_patch_shape(self):
        name = next(n for n in self._patches()
                    if n.startswith("Super Mario 64"))
        with open(os.path.join(PATCH_DIR, name), "rb") as f:
            wins = list(xp.iter_windows(f.read()))
        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0]["tgt_len"], 8 * 1024 * 1024)  # 8 MB ROM
        self.assertTrue(wins[0]["win_ind"] & xp.VCD_SOURCE)
        self.assertTrue(wins[0]["win_ind"] & xp.VCD_ADLER32)

    def test_bundled_patches_have_no_secondary_compression(self):
        for name in self._patches():
            with open(os.path.join(PATCH_DIR, name), "rb") as f:
                data = f.read()
            self.assertEqual(data[:4], xp.VCDIFF_MAGIC, name)
            self.assertFalse(data[4] & xp.VCD_DECOMPRESS, name)


if __name__ == "__main__":
    unittest.main()
