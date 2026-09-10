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

from n64patcher import datdb
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
        self.assertEqual(len(headers), 17)
        self.assertIn("SHA1", headers)
        self.assertIn("SubDrag patch", headers)
        self.assertIn("DAT match", headers)
        self.assertIn("Dump", headers)
        self.assertIn("Game fix", headers)


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


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestGameFixColumnVerifyAndLists(unittest.TestCase):
    """(a) which fix will apply per ROM, (b) verifying the last run's
    outputs, (c) removing single list entries."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ini = os.path.join(self.tmp.name, "settings.ini")

        def factory(*a, **k):
            return QSettings(self.ini, QSettings.Format.IniFormat)

        with mock.patch.object(gui, "QSettings", side_effect=factory):
            self.win = gui.N64PatcherGUI()
        self.addCleanup(self.win.close)

    def _inspect_one(self, crc):
        rom = make_rom(self.tmp.name, "sm64.z64", crc)
        self.win.rom_list = [rom]
        self.win.start_inspection()
        worker = self.win.inspect_worker
        assert worker is not None
        worker.wait(5000)
        QApplication.processEvents()

    def test_inspector_reports_which_game_fix_will_apply(self):
        # pin the lookup to the shipped fix: the machine's user-level
        # bisect file legitimately wins, but the column logic is the same
        shipped = os.path.join(core.GAME_FIXES_DIR, "635A2BFF_sm64_menu_2x.ips")
        self._inspect_one((0x635A2BFF, 0x8B022326))  # primed the row shape
        with mock.patch.object(gui.core, "get_game_fix_for_rom", return_value=shipped):
            self._inspect_one((0x635A2BFF, 0x8B022326))
        tree = self.win.tree
        item = tree.topLevelItem(0)
        assert item is not None
        fix_col = [tree.headerItem().text(i) for i in range(tree.columnCount())].index("Game fix")
        text = item.text(fix_col)
        self.assertIn("635A2BFF_sm64_menu_2x", text)
        self.assertNotIn("[user]", text)

    def test_inspector_shows_nothing_when_no_fix_matches(self):
        self._inspect_one((0xDEADBEEF, 0x12345678))
        tree = self.win.tree
        item = tree.topLevelItem(0)
        assert item is not None
        fix_col = [tree.headerItem().text(i) for i in range(tree.columnCount())].index("Game fix")
        self.assertEqual(item.text(fix_col), "")

    def test_verify_checks_the_outputs_of_the_last_run(self):
        src = os.path.join(self.tmp.name, "in.z64")
        out = os.path.join(self.tmp.name, "out [NoAA].z64")
        with open(src, "wb") as f:
            f.write(b"\x80\x37\x12\x40" + bytes(252))
        with open(out, "wb") as f:
            f.write(b"\x80\x37\x12\x40" + b"\x01" + bytes(251))
        self.win.last_outputs = [(out, {"NoAA"})]
        with mock.patch.object(gui.core, "verify_output", return_value={"ok": True, "checks": []}):
            self.win.start_verification()
            worker = self.win.verify_worker
            assert worker is not None
            worker.wait(5000)
            QApplication.processEvents()
        log = self.win.log_widget.toPlainText()
        self.assertIn("✓ out [NoAA].z64", log)
        self.assertIn("1 OK, 0 failed", log)
        self.assertTrue(self.win.btn_verify.isEnabled())

    def test_verify_reports_failing_checks(self):
        src_out = os.path.join(self.tmp.name, "bad [NoAA].z64")
        with open(src_out, "wb") as f:
            f.write(b"broken")
        self.win.last_outputs = [(src_out, set())]
        verdict = {
            "ok": False,
            "checks": [{"name": "boot checksums", "ok": False, "strict": True, "detail": "stale"}],
        }
        with mock.patch.object(gui.core, "verify_output", return_value=verdict):
            self.win.start_verification()
            worker = self.win.verify_worker
            assert worker is not None
            worker.wait(5000)
            QApplication.processEvents()
        self.assertIn(
            "❌ bad [NoAA].z64: boot checksums - stale", self.win.log_widget.toPlainText()
        )

    def test_a_single_rom_can_be_removed_from_the_list(self):
        keep = make_rom(self.tmp.name, "keep.z64", (0x11111111, 0x22222222))
        drop = make_rom(self.tmp.name, "drop.z64", (0x33333333, 0x44444444))
        self.win.rom_list = [keep, drop]
        self.win._hires_cache[drop] = False
        self.win._refresh_list_widgets()
        self.assertEqual(self.win.rom_list_widget.count(), 2)
        self.win._remove_roms([drop])
        self.assertEqual(self.win.rom_list, [keep])
        self.assertNotIn(drop, self.win._hires_cache)
        self.assertEqual(self.win.rom_list_widget.count(), 1)
        self.assertIn("1 ROM(s) removed", self.win.log_widget.toPlainText())

    def test_inspector_filter_hides_non_matching_rows(self):
        for i, title in enumerate(("MARIO", "ZELDA", "MARIO KART")):
            self.win.on_inspect_item(
                {
                    "filename": f"rom{i}.z64",
                    "title": title,
                    "region": "",
                    "format": "",
                    "size_mb": 1,
                    "no_aa": False,
                    "is_hires_640x480": False,
                    "vi_table_count": 0,
                    "crc1": "",
                    "crc2": "",
                    "has_subdrag_patch": False,
                }
            )
        self.win.filter_edit.setText("mario")
        visible = [
            self.win.tree.topLevelItem(i).isHidden()
            for i in range(self.win.tree.topLevelItemCount())
        ]
        self.assertEqual(visible, [False, True, False])
        self.win.filter_edit.setText("")
        visible = [
            self.win.tree.topLevelItem(i).isHidden()
            for i in range(self.win.tree.topLevelItemCount())
        ]
        self.assertEqual(visible, [False, False, False])

    def test_geometry_is_persisted_and_restored(self):
        self.win.save_settings()
        stored = QSettings(self.ini, QSettings.Format.IniFormat)
        self.assertTrue(bytes(stored.value("geometry")))


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestManifestAndDatParity(unittest.TestCase):
    """The two CLI powers the GUI now shares: undo manifests and DAT
    identification."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ini = os.path.join(self.tmp.name, "settings.ini")

        def factory(*a, **k):
            return QSettings(self.ini, QSettings.Format.IniFormat)

        with mock.patch.object(gui, "QSettings", side_effect=factory):
            self.win = gui.N64PatcherGUI()
        self.addCleanup(self.win.close)

    def test_hires_flavor_combo_reaches_the_patch_options(self):
        index = self.win.hires_flavor_combo.findData("640x240")
        self.assertGreaterEqual(index, 0, "640x240 flavor is not offered")
        self.win.hires_flavor_combo.setCurrentIndex(index)
        rom = make_rom(self.tmp.name, "plain.z64", (0xDEADBEEF, 0x12345678))
        self.win.rom_list = [rom]
        with mock.patch.object(gui.QMessageBox, "information"):
            self.win.start_patching()
            flavor = self.win.worker.options.hires_flavor
            self.win.worker.cancel()
            self.win.worker.wait(5000)
            QApplication.processEvents()
        self.assertEqual(flavor, "640x240")

    def test_manifest_flag_reaches_the_patch_options(self):
        self.win.cb_manifest.setChecked(True)
        rom = make_rom(self.tmp.name, "plain.z64", (0xDEADBEEF, 0x12345678))
        self.win.rom_list = [rom]
        with mock.patch.object(gui.QMessageBox, "information"):
            self.win.start_patching()
            options = self.win.worker.options
            self.win.worker.cancel()
            self.win.worker.wait(5000)
            QApplication.processEvents()
        self.assertTrue(options.write_manifest)

    def test_revert_recovers_the_original_bytes(self):
        from n64patcher import manifest as manifest_mod

        src = os.path.join(self.tmp.name, "orig.z64")
        patched = os.path.join(self.tmp.name, "out [NoAA].z64")
        original = b"\x80\x37\x12\x40" + bytes(range(256))
        changed = bytearray(original)
        changed[0x40] ^= 0xFF
        with open(src, "wb") as f:
            f.write(original)
        with open(patched, "wb") as f:
            f.write(bytes(changed))
        man = manifest_mod.build_manifest(src, patched, applied=["NoAA"])
        manifest_mod.write_manifest(man, patched)

        recovered = os.path.join(self.tmp.name, "recovered.z64")
        with (
            mock.patch.object(gui.QFileDialog, "getOpenFileName", return_value=(patched, "")),
            mock.patch.object(gui.QFileDialog, "getSaveFileName", return_value=(recovered, "")),
            mock.patch.object(gui.QMessageBox, "information"),
        ):
            self.win.revert_patch()

        with open(recovered, "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertIn("Reverted", self.win.log_widget.toPlainText())

    def test_revert_without_a_sidecar_warns_and_writes_nothing(self):
        patched = os.path.join(self.tmp.name, "lonely.z64")
        with open(patched, "wb") as f:
            f.write(b"\x00" * 64)
        warned = []
        with (
            mock.patch.object(gui.QFileDialog, "getOpenFileName", return_value=(patched, "")),
            mock.patch.object(
                gui.QMessageBox, "warning", side_effect=lambda *a, **k: warned.append(a)
            ),
        ):
            self.win.revert_patch()
        self.assertTrue(warned)

    def test_dat_checkbox_gives_the_worker_an_index(self):
        self.win.cb_dats.setChecked(True)
        self.win.rom_list = [make_rom(self.tmp.name, "r.z64", (0xDEADBEEF, 0x12345678))]
        index = datdb.DatIndex()
        index.by_sha1["0123456789ABCDEF"] = {"game": "Fake (test)", "name": "Fake"}
        index.sources = ["fake (test)"]
        with mock.patch.object(gui.datdb, "load_dats", return_value=index):
            self.win.start_inspection()
            worker = self.win.inspect_worker
            self.assertIsNotNone(worker)
            self.assertIs(worker.dat, index)
            worker.wait(5000)
            QApplication.processEvents()
        self.assertIsNone(self.win.inspect_worker or None)

    def test_no_dat_files_means_no_index_and_plain_inspection(self):
        self.win.cb_dats.setChecked(True)
        self.win.rom_list = [make_rom(self.tmp.name, "r.z64", (0xDEADBEEF, 0x12345678))]
        empty = datdb.DatIndex()
        with mock.patch.object(gui.datdb, "load_dats", return_value=empty):
            self.win.start_inspection()
            worker = self.win.inspect_worker
            worker.wait(5000)
            QApplication.processEvents()
            self.assertIsNone(worker.dat)
        self.assertIsNone(self.win.inspect_worker or None)
