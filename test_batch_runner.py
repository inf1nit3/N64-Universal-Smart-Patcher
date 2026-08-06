"""Unit tests for batch_runner (parallel engine)."""
import os
import tempfile
import unittest

import n64_core as core
from batch_runner import batch_patch_roms
from test_n64_core import make_synthetic_rom


class TestBatchRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_mixed_batch_counts_and_order(self):
        good1 = self._write("good1.z64", make_synthetic_rom(vi_tables=1))
        good2 = self._write("good2.z64", make_synthetic_rom(vi_tables=2))
        junk = self._write("junk.z64", b"\x00" * 1024)  # bad magic -> skipped
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        summary = batch_patch_roms([good1, junk, good2], opts,
                                   max_workers=2, log_func=lambda m: None)
        self.assertEqual(summary["patched"], 2)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["errors"], 0)
        inputs = [r["input"] for r in summary["results"]]
        self.assertEqual(inputs, [good1, junk, good2])  # stable input order
        for res in summary["results"]:
            if res["status"] == "patched":
                self.assertTrue(os.path.isfile(res["output"]))

    def test_output_dir_passthrough(self):
        src = self._write("rom.z64", make_synthetic_rom(vi_tables=1))
        outdir = os.path.join(self.tmp.name, "batch_out")
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)
        summary = batch_patch_roms([src], opts, max_workers=1,
                                   log_func=lambda m: None, output_dir=outdir)
        self.assertEqual(summary["patched"], 1)
        out = summary["results"][0]["output"]
        self.assertEqual(os.path.dirname(out), outdir)
        self.assertTrue(os.path.isfile(out))

    def test_exception_in_worker_becomes_error_result(self):
        src = self._write("rom.z64", make_synthetic_rom(vi_tables=1))
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True)

        def boom(*args, **kwargs):
            raise RuntimeError("worker exploded")

        import unittest.mock as mock
        with mock.patch.object(core, "patch_rom", boom):
            summary = batch_patch_roms([src], opts, max_workers=1,
                                       log_func=lambda m: None)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["results"][0]["status"], "error")
        self.assertEqual(summary["results"][0]["input"], src)


if __name__ == "__main__":
    unittest.main()
