"""GUI shell tests: menu bar, shortcuts, output folder, async workers.

Run offscreen like the other GUI tests. What is under test is not pixel
layout but the rules: everything that runs a job is reachable from the
keyboard, the chosen output folder actually reaches the patch call, and
the background workers keep their state machine (busy -> done) honest.
"""

import importlib.util
import os
import tempfile
import time
import unittest
from unittest import mock

from n64patcher import n64_core as core

try:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication

    from n64patcher import gui

    HAVE_QT = True
except ImportError:  # pragma: no cover - environment without PyQt6
    HAVE_QT = False

HAVE_QT = HAVE_QT and importlib.util.find_spec("PyQt6") is not None

if HAVE_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        _app = QApplication.instance() or QApplication([])


def drain_hires_scan(win):
    """Pump the event loop until the hi-res scan worker is done."""
    deadline = time.monotonic() + 10
    while win._hires_scan_worker is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


def make_rom(directory, name, crc):
    from tests.test_n64_core import make_synthetic_rom

    rom = bytearray(make_synthetic_rom(vi_tables=0, size=0x4000))
    rom[0x10:0x14] = crc[0].to_bytes(4, "big")
    rom[0x14:0x18] = crc[1].to_bytes(4, "big")
    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(bytes(rom))
    return path


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestMenuBarAndShortcuts(unittest.TestCase):
    def setUp(self):
        self.win = gui.N64PatcherGUI()
        self.addCleanup(self.win.close)

    def test_the_bar_has_the_expected_menus(self):
        titles = [a.text() for a in self.win.menuBar().actions()]
        for expected in ("&File", "&Action", "&View", "&Help"):
            self.assertIn(expected, titles)

    def test_run_controls_are_connected(self):
        for action in (self.win.act_start, self.win.act_inspect, self.win.act_cancel):
            self.assertGreater(action.receivers(action.triggered), 0)

    def test_run_controls_carry_shortcuts(self):
        for action in (self.win.act_start, self.win.act_inspect):
            self.assertFalse(action.shortcut().isEmpty(), f"{action.text()} has no shortcut")

    def test_cancel_starts_disabled_and_tracks_the_run(self):
        self.assertFalse(self.win.act_cancel.isEnabled())
        self.assertFalse(self.win.btn_cancel.isEnabled())
        # The modal end-of-run boxes are stubbed: they would block a
        # headless run forever.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rom = make_rom(self.tmp.name, "plain.z64", (0xDEADBEEF, 0x12345678))
        self.win.rom_list = [rom]
        with mock.patch.object(gui.QMessageBox, "information"):
            self.win.start_patching()
            self.assertFalse(self.win.act_start.isEnabled())
            self.assertFalse(self.win.btn_patch.isEnabled())
            self.assertTrue(self.win.act_cancel.isEnabled())
            self.win.worker.cancel()
            self.win.worker.wait(5000)
            # Deliver the queued 'done' while the modal is stubbed, or it
            # surfaces in whatever test pumps the event loop next.
            QApplication.processEvents()

    def test_tab_shortcuts_switch_tabs(self):
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        self.win._tab_actions[2].trigger()
        self.assertEqual(self.win.tabs.currentIndex(), 2)


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestOutputFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Real QSettings would leak test values into the user's registry
        # or plist; point the window at a throwaway ini file instead.
        self.ini = os.path.join(self.tmp.name, "settings.ini")

        def factory(*a, **k):
            return QSettings(self.ini, QSettings.Format.IniFormat)

        with mock.patch.object(gui, "QSettings", side_effect=factory):
            self.win = gui.N64PatcherGUI()
        self.addCleanup(self.win.close)

    def test_defaults_to_next_to_each_rom(self):
        self.assertEqual(self.win.output_dir_edit.text(), "")
        self.assertEqual(self.win._patch_output_dir(), None)

    def test_choosing_a_folder_updates_the_field_and_settings(self):
        with mock.patch.object(gui.QFileDialog, "getExistingDirectory", return_value=self.tmp.name):
            self.win.choose_output_dir()
        self.assertEqual(self.win.output_dir_edit.text(), self.tmp.name)
        self.assertEqual(self.win._patch_output_dir(), self.tmp.name)
        self.win.save_settings()
        stored = QSettings(self.ini, QSettings.Format.IniFormat)
        self.assertEqual(stored.value("output_dir", type=str), self.tmp.name)

    def test_reset_clears_field_and_setting(self):
        with mock.patch.object(gui.QFileDialog, "getExistingDirectory", return_value=self.tmp.name):
            self.win.choose_output_dir()
        self.win.reset_output_dir()
        self.assertEqual(self.win.output_dir_edit.text(), "")
        self.assertEqual(self.win._patch_output_dir(), None)

    def test_a_missing_saved_folder_falls_back_to_the_default(self):
        stored = QSettings(self.ini, QSettings.Format.IniFormat)
        stored.setValue("output_dir", os.path.join(self.tmp.name, "gone"))
        stored.sync()

        def factory(*a, **k):
            return QSettings(self.ini, QSettings.Format.IniFormat)

        with mock.patch.object(gui, "QSettings", side_effect=factory):
            win = gui.N64PatcherGUI()
        self.addCleanup(win.close)
        self.assertEqual(win.output_dir_edit.text(), "")

    def test_the_folder_reaches_the_patch_worker(self):
        with mock.patch.object(gui.QFileDialog, "getExistingDirectory", return_value=self.tmp.name):
            self.win.choose_output_dir()
        rom = make_rom(self.tmp.name, "plain.z64", (0xDEADBEEF, 0x12345678))
        self.win.rom_list = [rom]
        self.win.act_start.trigger()
        self.addCleanup(lambda: self.win.worker and self.win.worker.wait(5000))
        self.assertIsNotNone(self.win.worker)
        self.assertEqual(self.win.worker.output_dir, self.tmp.name)
        with mock.patch.object(gui.QMessageBox, "information"):
            self.win.worker.cancel()
            self.win.worker.wait(5000)
            QApplication.processEvents()


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestAsyncHiresScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.win = gui.N64PatcherGUI()
        self.addCleanup(self.win.close)

    def test_the_scan_runs_in_the_background_and_lands(self):
        verified = next(iter(core.SUBDRAG_PATCHES))
        rom = make_rom(self.tmp.name, "sm64.z64", verified)
        self.win.rom_list = [rom]
        self.win.update_hires_availability()
        # Not synchronous: the box reports the pending check, not a verdict.
        self.assertIn("checking", self.win.cb_hires.text().lower())
        drain_hires_scan(self.win)
        self.assertTrue(self.win.cb_hires.isEnabled())
        self.assertIn("verified for 1", self.win.cb_hires.text())

    def test_results_are_cached_no_rescan_on_reupdate(self):
        verified = next(iter(core.SUBDRAG_PATCHES))
        rom = make_rom(self.tmp.name, "sm64.z64", verified)
        self.win.rom_list = [rom]
        self.win.update_hires_availability()
        drain_hires_scan(self.win)
        self.assertIsNone(self.win._hires_scan_worker)
        self.win.update_hires_availability()
        self.assertIsNone(self.win._hires_scan_worker)

    def test_inspector_table_has_a_column_per_header(self):
        tree = self.win.tree
        headers = [tree.headerItem().text(i) for i in range(tree.columnCount())]
        self.assertEqual(len(headers), 14)
        self.assertIn("SHA1", headers)
        self.assertIn("SubDrag patch", headers)


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestFolderOfSaves(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.win = gui.N64PatcherGUI()
        self.addCleanup(self.win.close)

    def test_a_dropped_folder_routes_its_saves_to_the_saves_tab(self):
        sub = os.path.join(self.tmp.name, "saves")
        os.makedirs(sub)
        src = os.path.join(sub, "progress.sra")
        with open(src, "wb") as f:
            f.write(b"\x00" * 512)
        with open(os.path.join(sub, "notes.txt"), "w") as f:
            f.write("not a save")

        self.win.add_paths([self.tmp.name])

        self.assertEqual(self.win.save_list, [src])
        self.assertEqual(self.win.rom_list, [])


if __name__ == "__main__":
    unittest.main()
