"""GUI-side hi-res gating, run headless via Qt's offscreen platform.

Regression: the 640x480 checkbox was always clickable, so users could ask
for the generic VI-table widening on any ROM. That renders incorrectly on
hardware (doubled image, misplaced UI), confirmed on a SummerCart64. The
box must be disabled unless a loaded ROM has a verified per-dump patch.
"""
import importlib.util
import os
import tempfile
import unittest

from n64patcher import n64_core as core
from tests.test_n64_core import make_synthetic_rom

HAVE_QT = importlib.util.find_spec("PyQt6") is not None
if HAVE_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestGuiHiresGate(unittest.TestCase):
    app = None
    gui = None

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        from n64patcher import gui as gui_mod

        cls.app = QApplication.instance() or QApplication([])
        cls.gui_mod = gui_mod
        cls.gui = gui_mod.N64PatcherGUI()

    @classmethod
    def tearDownClass(cls):
        if cls.gui is not None:
            cls.gui.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gui.rom_list = []

    def _rom(self, name, crc=(0xDEADBEEF, 0x12345678), hires=False):
        rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x4000))
        rom[0x10:0x14] = crc[0].to_bytes(4, "big")
        rom[0x14:0x18] = crc[1].to_bytes(4, "big")
        width = core.WIDTH_640_DATA if hires else core.WIDTH_320_DATA
        for i in range(2):
            off = 0x1000 + i * 0x40
            rom[off:off + 4] = width
            rom[off + 4:off + 8] = core.NTSC_BURST
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(bytes(rom))
        return p

    def test_empty_list_leaves_box_usable(self):
        self.gui.update_hires_availability()
        self.assertTrue(self.gui.cb_hires.isEnabled())
        self.assertIn("Load ROMs", self.gui.cb_hires.toolTip())

    def test_unsupported_rom_disables_and_unchecks_box(self):
        """The fix: an ordinary ROM must not be able to request 640x480."""
        self.gui.cb_hires.setChecked(True)
        self.gui.rom_list = [self._rom("plain.z64")]
        self.gui.update_hires_availability()

        self.assertFalse(self.gui.cb_hires.isEnabled())
        self.assertFalse(self.gui.cb_hires.isChecked())
        self.assertIn("not available", self.gui.cb_hires.text())
        tip = self.gui.cb_hires.toolTip()
        self.assertIn("framebuffer", tip)
        self.assertIn("doubled image", tip)

    def test_verified_rom_enables_box(self):
        verified = next(iter(core.SUBDRAG_PATCHES))
        self.gui.rom_list = [self._rom("sm64.z64", crc=verified)]
        self.gui.update_hires_availability()
        self.assertTrue(self.gui.cb_hires.isEnabled())
        self.assertIn("verified for 1", self.gui.cb_hires.text())

    def test_mixed_list_enables_and_reports_the_count(self):
        verified = next(iter(core.SUBDRAG_PATCHES))
        self.gui.rom_list = [
            self._rom("sm64.z64", crc=verified),
            self._rom("plain1.z64"),
            self._rom("plain2.z64"),
        ]
        self.gui.update_hires_availability()
        self.assertTrue(self.gui.cb_hires.isEnabled())
        self.assertIn("verified for 1 of 3", self.gui.cb_hires.text())

    def test_native_hires_rom_does_not_enable_the_box(self):
        """Already 640x480: nothing to apply, so the box stays off."""
        self.gui.rom_list = [self._rom("native.z64", hires=True)]
        self.gui.update_hires_availability()
        self.assertFalse(self.gui.cb_hires.isEnabled())

    def test_gui_never_sets_force_hires(self):
        """force_hires is a deliberate CLI-only escape hatch."""
        self.assertNotIn("force_hires", self.gui_mod.__dict__)
        src = os.path.join(os.path.dirname(self.gui_mod.__file__), "gui.py")
        with open(src, encoding="utf-8") as f:
            self.assertNotIn("force_hires", f.read())


if __name__ == "__main__":
    unittest.main()
