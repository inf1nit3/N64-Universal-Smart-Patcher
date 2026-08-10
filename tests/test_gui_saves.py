"""GUI tests for the Saves tab.

Run offscreen; skipped where PyQt6 is not installed, as the other GUI
tests are. The point of interest is not the widgets but the rules they
have to keep: an existing save is never replaced without being asked
about, and the byte order is chosen, never detected.
"""

import os
import tempfile
import unittest

from n64patcher import savegame as sg

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from n64patcher import gui
    HAVE_QT = True
except ImportError:  # pragma: no cover - environment without PyQt6
    HAVE_QT = False

_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QApplication.instance() or QApplication([])


def oot_chip_order():
    blob = bytearray(b"\xFF" * (32 * 1024))
    blob[:len(sg.OOT_HEADER)] = sg.OOT_HEADER
    for off in sg.OOT_MARKER_OFFSETS:
        blob[off:off + len(sg.OOT_MARKER)] = sg.OOT_MARKER
    return bytes(blob)


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestSaveTab(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.win = gui.N64PatcherGUI()
        self.addCleanup(self.win.close)
        self.src = self._write("THE LEGEND OF ZELDA-9EB1E8AC.sra",
                               sg.swap_words(oot_chip_order()))

    def _write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def _select(self, source, target):
        self.win.save_from.setCurrentIndex(
            self.win.save_from.findData(source))
        self.win.save_to.setCurrentIndex(self.win.save_to.findData(target))

    def test_the_tab_offers_every_measured_tool(self):
        keys = {self.win.save_from.itemData(i)
                for i in range(self.win.save_from.count())}
        self.assertEqual(keys, set(sg.SOURCES_BY_KEY))

    def test_adding_and_clearing(self):
        self.assertEqual(self.win.add_saves([self.src]), 1)
        self.assertEqual(self.win.save_list, [self.src])
        # The same file twice stays one entry.
        self.assertEqual(self.win.add_saves([self.src]), 0)
        self.win.clear_saves()
        self.assertEqual(self.win.save_list, [])

    def test_a_dropped_save_lands_in_the_save_list_not_the_rom_list(self):
        self.win.add_paths([self.src])
        self.assertEqual(self.win.save_list, [self.src])
        self.assertEqual(self.win.rom_list, [])

    def test_inspect_reports_the_game_and_the_arrangement(self):
        self.win.add_saves([self.src])
        self.win.inspect_saves()
        text = self.win.save_output.toPlainText()
        self.assertIn("SRAM 256 Kbit", text)
        self.assertIn("Ocarina of Time", text)

    def test_convert_writes_the_flashcart_arrangement(self):
        out_dir = os.path.join(self.tmp.name, "out")
        os.makedirs(out_dir)
        self.win.add_saves([self.src])
        self._select("mupen64plus", "sc64")
        gui.QFileDialog.getExistingDirectory = staticmethod(
            lambda *a, **k: out_dir)

        self.win.convert_saves()

        written = os.listdir(out_dir)
        self.assertEqual(len(written), 1, written)
        self.assertEqual(self._read(os.path.join(out_dir, written[0])),
                         oot_chip_order())

    def test_an_existing_save_is_kept_when_the_user_declines(self):
        """The rule that matters: progress is not replaced behind the
        user's back, and answering No leaves the file exactly as it was."""
        out_dir = os.path.join(self.tmp.name, "out")
        os.makedirs(out_dir)
        existing = os.path.join(
            out_dir, "THE LEGEND OF ZELDA-9EB1E8AC [sc64].sav")
        with open(existing, "wb") as f:
            f.write(b"someone's actual progress")

        self.win.add_saves([self.src])
        self._select("mupen64plus", "sc64")
        gui.QFileDialog.getExistingDirectory = staticmethod(
            lambda *a, **k: out_dir)
        gui.QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.No)

        self.win.convert_saves()

        self.assertEqual(self._read(existing), b"someone's actual progress")
        self.assertIn("kept the existing file",
                      self.win.save_output.toPlainText())

    def test_it_is_replaced_only_when_the_user_agrees(self):
        out_dir = os.path.join(self.tmp.name, "out")
        os.makedirs(out_dir)
        existing = os.path.join(
            out_dir, "THE LEGEND OF ZELDA-9EB1E8AC [sc64].sav")
        with open(existing, "wb") as f:
            f.write(b"old")

        self.win.add_saves([self.src])
        self._select("mupen64plus", "sc64")
        gui.QFileDialog.getExistingDirectory = staticmethod(
            lambda *a, **k: out_dir)
        gui.QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes)

        self.win.convert_saves()

        self.assertEqual(self._read(existing), oot_chip_order())

    def test_a_cancelled_folder_dialog_writes_nothing(self):
        self.win.add_saves([self.src])
        gui.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
        self.win.convert_saves()
        self.assertEqual(self.win.save_output.toPlainText(), "")


if __name__ == "__main__":
    unittest.main()
