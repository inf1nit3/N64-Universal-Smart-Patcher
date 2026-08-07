"""Unit tests for presets (immutability guarantees)."""
import unittest

from n64patcher import n64_core as core
from n64patcher.presets import PRESETS, apply_preset, get_preset_warnings, list_presets


class TestPresets(unittest.TestCase):
    def test_all_presets_consistent(self):
        self.assertIn("crt_authentic", PRESETS)
        self.assertIn("modern_crisp", PRESETS)
        self.assertIn("modern_4k", PRESETS)
        self.assertIn("speedrun", PRESETS)
        for key, preset in PRESETS.items():
            self.assertEqual(preset.key, key)
            self.assertIsInstance(preset.options, core.PatchOptions)

    def test_apply_preset_returns_fresh_copies(self):
        for key in PRESETS:
            first = apply_preset(key)
            second = apply_preset(key)
            self.assertIsNot(first, second, "apply_preset must not share instances")
            # Mutating the returned options must not affect the preset
            first.no_aa = not first.no_aa
            first.no_dither = not first.no_dither
            first.hires = not first.hires
            pristine = PRESETS[key].options
            third = apply_preset(key)
            self.assertEqual(third.no_aa, pristine.no_aa)
            self.assertEqual(third.no_dither, pristine.no_dither)
            self.assertEqual(third.hires, pristine.hires)

    def test_unknown_preset_returns_default(self):
        opts = apply_preset("does_not_exist")
        self.assertIsInstance(opts, core.PatchOptions)

    def test_list_presets_shape(self):
        listed = list_presets()
        self.assertEqual(len(listed), len(PRESETS))
        for entry in listed:
            self.assertIn("key", entry)
            self.assertIn("name", entry)
            self.assertIn("description", entry)

    def test_warnings_are_lists(self):
        for key in PRESETS:
            self.assertIsInstance(get_preset_warnings(key), list)
        self.assertIsInstance(get_preset_warnings("nope"), list)


if __name__ == "__main__":
    unittest.main()
