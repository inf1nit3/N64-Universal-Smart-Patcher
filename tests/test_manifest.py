"""Unit tests for undo manifests.

The pipeline never modifies an original, so these are about *auditability*:
proving what a patch changed, and being able to undo it from the output
alone. A manifest that looks reversible but is not would be worse than none,
so the refusal paths get as much attention as the happy one.
"""
import json
import os
import tempfile
import unittest

from n64patcher import manifest
from n64patcher import n64_core as core
from tests.test_n64_core import make_cic6102_rom, make_synthetic_rom


class TestDiffRuns(unittest.TestCase):
    def test_identical_inputs_produce_nothing(self):
        data = b"hello world"
        runs, total, complete = manifest.diff_runs(data, data)
        self.assertEqual((runs, total, complete), ([], 0, True))

    def test_single_run(self):
        runs, total, complete = manifest.diff_runs(b"AAAA", b"ABBA")
        self.assertTrue(complete)
        self.assertEqual(total, 2)
        self.assertEqual(runs, [{"offset": 1, "old": "4141", "new": "4242"}])

    def test_separate_runs_are_not_merged(self):
        runs, _, _ = manifest.diff_runs(b"AAAAAA", b"BAAAAB")
        self.assertEqual([r["offset"] for r in runs], [0, 5])

    def test_run_spanning_a_block_boundary(self):
        """Blocks are compared wholesale first; a run crossing the seam
        must still come out as one run."""
        size = manifest._BLOCK * 2
        before = bytes(size)
        after = bytearray(before)
        start = manifest._BLOCK - 3
        after[start:start + 6] = b"\x01" * 6
        runs, total, complete = manifest.diff_runs(before, bytes(after))
        self.assertTrue(complete)
        self.assertEqual(total, 6)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["offset"], start)

    def test_growth_is_recorded(self):
        runs, total, _ = manifest.diff_runs(b"AA", b"AABB")
        self.assertEqual(total, 2)
        self.assertEqual(runs[0]["old"], "")
        self.assertEqual(runs[0]["new"], "4242")

    def test_truncation_is_recorded(self):
        runs, total, _ = manifest.diff_runs(b"AABB", b"AA")
        self.assertEqual(total, 2)
        self.assertEqual(runs[0]["new"], "")

    def test_cap_marks_incomplete_and_stops_storing(self):
        before = bytes(4096)
        after = bytes([0xFF]) * 4096
        runs, total, complete = manifest.diff_runs(before, after, max_bytes=100)
        self.assertFalse(complete)
        self.assertLessEqual(sum(len(r["new"]) // 2 for r in runs), 4096)
        self.assertGreater(total, 100)


class ManifestTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _pair(self, mutate=None, name="in.z64", out_name="out.z64",
              size=0x4000):
        data = bytearray(make_synthetic_rom(vi_tables=1, size=size))
        src = os.path.join(self.tmp.name, name)
        with open(src, "wb") as f:
            f.write(bytes(data))
        if mutate:
            mutate(data)
        dst = os.path.join(self.tmp.name, out_name)
        with open(dst, "wb") as f:
            f.write(bytes(data))
        return src, dst


class TestBuildAndRevert(ManifestTestBase):
    def _mutate(self, data):
        data[0x1000:0x1004] = core.WIDTH_640_DATA
        data[0x2000] ^= 0xFF

    def test_manifest_records_the_changes(self):
        src, dst = self._pair(self._mutate)
        man = manifest.build_manifest(src, dst, applied={"HR"}, stages=["test"])
        self.assertTrue(man["revertible"])
        # 00000140 -> 00000280 differs in 2 bytes, plus the flipped one.
        self.assertEqual(man["changed_bytes"], 3)
        self.assertEqual(man["applied"], ["HR"])
        self.assertEqual(man["stages"], ["test"])
        self.assertEqual(man["input"]["name"], "in.z64")

    def test_roundtrip_restores_byte_for_byte(self):
        src, dst = self._pair(self._mutate)
        man = manifest.build_manifest(src, dst)
        restored = os.path.join(self.tmp.name, "restored.z64")
        ok, msg = manifest.revert(dst, man, restored)
        self.assertTrue(ok, msg)
        with open(src, "rb") as a, open(restored, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_roundtrip_through_a_written_file(self):
        src, dst = self._pair(self._mutate)
        path = manifest.write_manifest(manifest.build_manifest(src, dst), dst)
        self.assertTrue(path.endswith(manifest.MANIFEST_SUFFIX))
        loaded = manifest.load_manifest(path)
        restored = os.path.join(self.tmp.name, "restored.z64")
        ok, _ = manifest.revert(dst, loaded, restored)
        self.assertTrue(ok)

    def test_revert_restores_a_size_change(self):
        src, dst = self._pair(lambda d: d.extend(b"\xAA" * 64))
        man = manifest.build_manifest(src, dst)
        restored = os.path.join(self.tmp.name, "r.z64")
        ok, msg = manifest.revert(dst, man, restored)
        self.assertTrue(ok, msg)
        self.assertEqual(os.path.getsize(restored), os.path.getsize(src))

    # --- refusals ---------------------------------------------------------

    def test_refuses_a_different_file(self):
        src, dst = self._pair(self._mutate)
        man = manifest.build_manifest(src, dst)
        with open(dst, "r+b") as f:
            f.seek(0x3000)
            f.write(b"\x99")
        ok, msg = manifest.revert(dst, man,
                                  os.path.join(self.tmp.name, "r.z64"))
        self.assertFalse(ok)
        self.assertIn("different file", msg)

    def test_refuses_when_not_revertible(self):
        # Bigger than MAX_RECORDED_BYTES and fully rewritten, which is the
        # shape of a delta-patched ROM.
        src, dst = self._pair(
            lambda d: d.__setitem__(slice(0, len(d)), bytes([0xFF]) * len(d)),
            size=manifest.MAX_RECORDED_BYTES * 2)
        man = manifest.build_manifest(src, dst)
        self.assertFalse(man["revertible"])
        ok, msg = manifest.revert(dst, man,
                                  os.path.join(self.tmp.name, "r.z64"))
        self.assertFalse(ok)
        self.assertIn("Keep the input ROM", msg)

    def test_unsupported_manifest_version_rejected(self):
        src, dst = self._pair(self._mutate)
        path = manifest.write_manifest(manifest.build_manifest(src, dst), dst)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["manifest_version"] = 99
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaises(ValueError) as ctx:
            manifest.load_manifest(path)
        self.assertIn("manifest_version", str(ctx.exception))

    def test_describe_lists_the_runs(self):
        src, dst = self._pair(self._mutate)
        text = manifest.describe(manifest.build_manifest(src, dst))
        self.assertIn("changed", text)
        self.assertIn("0x00001002", text)


class TestPipelineIntegration(ManifestTestBase):
    def _rom(self, name="game.z64"):
        # CIC-6102 so the pipeline can actually stamp CRC1/CRC2.
        rom = bytearray(make_cic6102_rom(size=0x4000))
        rom[0x1400:0x1404] = core.AA_PATTERN
        for i in range(2):
            off = 0x1800 + i * 0x40
            rom[off:off + 4] = core.WIDTH_320_DATA
            rom[off + 4:off + 8] = core.NTSC_BURST
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(bytes(rom))
        return p

    def test_no_manifest_unless_requested(self):
        src = self._rom()
        opts = core.PatchOptions(no_aa=True, no_dither=False, hires=False)
        res = core.patch_rom(src, opts, log=lambda m: None)
        self.assertEqual(res["status"], "patched", res)
        self.assertNotIn("manifest", res)
        self.assertFalse([f for f in os.listdir(self.tmp.name)
                          if f.endswith(manifest.MANIFEST_SUFFIX)])

    def test_manifest_written_and_reverts(self):
        src = self._rom()
        with open(src, "rb") as f:
            original = f.read()
        opts = core.PatchOptions(no_aa=True, no_dither=False, hires=False,
                                 write_manifest=True)
        res = core.patch_rom(src, opts, log=lambda m: None)
        self.assertEqual(res["status"], "patched", res)
        self.assertTrue(os.path.isfile(res["manifest"]))

        man = manifest.load_manifest(res["manifest"])
        self.assertIn("dynamic-vi:NoAA", man["stages"])
        restored = os.path.join(self.tmp.name, "restored.z64")
        ok, msg = manifest.revert(res["output"], man, restored)
        self.assertTrue(ok, msg)
        with open(restored, "rb") as f:
            self.assertEqual(f.read(), original)

    def test_manifest_records_the_crc_header_write(self):
        """The CRC stamp is a real change and must appear in the audit."""
        src = self._rom()
        opts = core.PatchOptions(no_aa=True, no_dither=False, hires=False,
                                 write_manifest=True)
        res = core.patch_rom(src, opts, log=lambda m: None)
        man = manifest.load_manifest(res["manifest"])
        touched = set()
        for run in man["runs"]:
            length = max(len(run["old"]), len(run["new"])) // 2
            touched.update(range(run["offset"], run["offset"] + length))
        self.assertTrue(touched & set(range(0x10, 0x18)),
                        "CRC1/CRC2 header write is missing from the manifest")


if __name__ == "__main__":
    unittest.main()
