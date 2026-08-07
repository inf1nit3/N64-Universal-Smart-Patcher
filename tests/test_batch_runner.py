"""Unit tests for batch_runner (parallel engine)."""
import os
import tempfile
import threading
import unittest

from n64patcher import n64_core as core
from n64patcher.batch_runner import LogPump, batch_patch_roms
from tests.test_n64_core import make_synthetic_rom


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
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
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
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        summary = batch_patch_roms([src], opts, max_workers=1,
                                   log_func=lambda m: None, output_dir=outdir)
        self.assertEqual(summary["patched"], 1)
        out = summary["results"][0]["output"]
        self.assertEqual(os.path.dirname(out), outdir)
        self.assertTrue(os.path.isfile(out))

    def test_exception_in_worker_becomes_error_result(self):
        src = self._write("rom.z64", make_synthetic_rom(vi_tables=1))
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump

        def boom(*args, **kwargs):
            raise RuntimeError("worker exploded")

        import unittest.mock as mock
        with mock.patch.object(core, "patch_rom", boom):
            summary = batch_patch_roms([src], opts, max_workers=1,
                                       log_func=lambda m: None)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["results"][0]["status"], "error")
        self.assertEqual(summary["results"][0]["input"], src)


class TestBatchCancellation(unittest.TestCase):
    """Regression: batch_patch_roms hard-coded `lambda: False` as
    should_cancel, so every cancellation check inside patch_rom was dead
    code and a running batch could not be stopped."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _roms(self, n):
        paths = []
        for i in range(n):
            p = os.path.join(self.tmp.name, f"rom{i}.z64")
            with open(p, "wb") as f:
                f.write(make_synthetic_rom(vi_tables=1))
            paths.append(p)
        return paths

    def test_cancel_flag_reaches_workers(self):
        roms = self._roms(8)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        summary = batch_patch_roms(roms, opts, max_workers=2,
                                   log_func=lambda m: None,
                                   should_cancel=lambda: True)
        self.assertEqual(summary["patched"], 0)
        self.assertEqual(summary["skipped"], len(roms))
        self.assertTrue(summary["cancelled"])
        self.assertTrue(all(r["status"] == "cancelled" for r in summary["results"]))
        # Nothing was written for a cancelled run.
        produced = [f for f in os.listdir(self.tmp.name) if "[" in f]
        self.assertEqual(produced, [])

    def test_cancel_midway_still_reports_finished_work(self):
        roms = self._roms(10)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        seen = threading.Event()
        done = []
        lock = threading.Lock()

        def should_cancel():
            with lock:
                return len(done) >= 3

        real_patch = core.patch_rom

        def counting_patch(*args, **kwargs):
            res = real_patch(*args, **kwargs)
            if res["status"] == "patched":
                with lock:
                    done.append(res)
                seen.set()
            return res

        import unittest.mock as mock
        with mock.patch.object(core, "patch_rom", counting_patch):
            summary = batch_patch_roms(roms, opts, max_workers=1,
                                       log_func=lambda m: None,
                                       should_cancel=should_cancel)
        self.assertTrue(seen.is_set())
        self.assertGreaterEqual(summary["patched"], 3)
        self.assertLess(summary["patched"], len(roms))
        self.assertEqual(summary["patched"] + summary["skipped"], len(roms))

    def test_default_is_not_cancelled(self):
        roms = self._roms(2)
        opts = core.PatchOptions(no_aa=False, no_dither=False, hires=True,
                                 force_hires=True)  # synthetic fixture: no verified dump
        summary = batch_patch_roms(roms, opts, max_workers=2,
                                   log_func=lambda m: None)
        self.assertFalse(summary["cancelled"])
        self.assertEqual(summary["patched"], 2)


class TestLogPump(unittest.TestCase):
    """Regression: log_func was invoked from N worker threads at once."""

    def test_sink_is_called_on_one_thread_only(self):
        threads = set()
        received = []

        def sink(msg):
            threads.add(threading.get_ident())
            received.append(msg)

        with LogPump(sink) as pump:
            workers = [threading.Thread(target=lambda n=n: [pump(f"{n}-{i}")
                                                            for i in range(50)])
                       for n in range(6)]
            for w in workers:
                w.start()
            for w in workers:
                w.join()

        self.assertEqual(len(received), 300)
        self.assertEqual(len(threads), 1, "sink saw more than one thread")
        self.assertNotEqual(threads.pop(), threading.get_ident())

    def test_failing_sink_does_not_kill_the_pump(self):
        received = []

        def sink(msg):
            if msg == "boom":
                raise RuntimeError("sink failed")
            received.append(msg)

        with LogPump(sink) as pump:
            pump("a")
            pump("boom")
            pump("b")

        self.assertEqual(received, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
