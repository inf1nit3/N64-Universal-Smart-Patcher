"""CLI-level tests for hi-res option assembly.

Two real bugs once lived in this layer: --force-hires lost its warning
to a mis-indented log call (and --h2x printed a false warning instead),
and --h2x combined with a preset silently produced the 640x480 build
instead of the requested 640x240. _assemble_options is the extracted,
directly testable seam for that assembly.
"""

import argparse
import unittest

from n64patcher import cli


def make_args(**kw):
    base = {
        "preset": None,
        "keep_aa": False,
        "no_dither": False,
        "no_divot": False,
        "no_gamma": False,
        "hires": False,
        "h2x": False,
        "force_hires": False,
        "manifest": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class AssembleOptionsTest(unittest.TestCase):
    def _assemble(self, **kw):
        logs = []
        options = cli._assemble_options(make_args(**kw), logs.append)
        return options, logs

    def test_h2x_alone_requests_hi_res_640x240(self):
        options, _ = self._assemble(h2x=True)
        self.assertTrue(options.hires)
        self.assertEqual(options.hires_flavor, "640x240")

    def test_h2x_with_preset_keeps_the_640x240_flavor(self):
        """Regression: the preset branch once ignored --h2x and silently
        applied the 640x480 SubDrag build instead."""
        options, _ = self._assemble(preset="modern_4k", h2x=True)
        self.assertTrue(options.hires)
        self.assertEqual(options.hires_flavor, "640x240")

    def test_preset_without_h2x_stays_640x480(self):
        options, _ = self._assemble(preset="modern_4k")
        self.assertTrue(options.hires)
        self.assertEqual(options.hires_flavor, "640x480")

    def test_force_hires_warns_and_h2x_does_not(self):
        """Regression: the warning was once gated to --h2x; --force-hires
        ran silent and --h2x lied about widening."""
        options, logs = self._assemble(force_hires=True)
        self.assertTrue(options.hires)
        self.assertEqual(options.hires_flavor, "640x480")
        self.assertTrue(any("--force-hires" in line for line in logs))

        options, logs = self._assemble(h2x=True)
        self.assertFalse(any("--force-hires" in line for line in logs))

    def test_plain_run_is_640x480_and_unforced(self):
        options, logs = self._assemble(hires=True)
        self.assertTrue(options.hires)
        self.assertFalse(options.force_hires)
        self.assertEqual(options.hires_flavor, "640x480")
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
