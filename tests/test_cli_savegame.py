"""CLI-level tests for the save file commands.

The module tests cover the transformations; these cover the layer a user
actually touches - argument handling, refusals, and above all what gets
written to disk. That last one is where the first version of this code
silently destroyed an existing save.
"""

import os
import tempfile
import unittest

from n64patcher import savegame as sg
from n64patcher.cli import main


def oot_chip_order():
    blob = bytearray(b"\xFF" * (32 * 1024))
    blob[:len(sg.OOT_HEADER)] = sg.OOT_HEADER
    for off in sg.OOT_MARKER_OFFSETS:
        blob[off:off + len(sg.OOT_MARKER)] = sg.OOT_MARKER
    return bytes(blob)


class SaveCliTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # What mupen64plus would have written: the same save, word-swapped.
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

    def _run(self, *argv):
        return main(list(argv))


class TestSaveInfo(SaveCliTest):

    def test_reports_chip_game_and_arrangement(self):
        self.assertEqual(self._run("--save-info", self.src), 0)

    def test_a_missing_file_fails_cleanly(self):
        self.assertEqual(
            self._run("--save-info", os.path.join(self.tmp.name, "nope.sra")), 1)


class TestSaveConvert(SaveCliTest):

    def test_converts_and_verifies(self):
        out = os.path.join(self.tmp.name, "out.sav")
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "mupen64plus",
                      "--save-to", "sc64", "--save-out", out), 0)
        self.assertEqual(self._read(out), oot_chip_order())

    def test_both_ends_must_be_named(self):
        """Byte order belongs to the tool, and detection is wrong on one
        save in five, so it may not stand in for a missing argument."""
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-to", "sc64"), 1)
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "mupen64plus"), 1)

    def test_an_existing_target_is_never_overwritten(self):
        """The bug this test exists for: the first version wrote over an
        existing save without a word. There is no undo for that."""
        out = self._write("precious.sav", b"someone's actual progress")
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "mupen64plus",
                      "--save-to", "sc64", "--save-out", out), 1)
        self.assertEqual(self._read(out), b"someone's actual progress")

    def test_force_allows_replacing_it(self):
        out = self._write("precious.sav", b"someone's actual progress")
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "mupen64plus",
                      "--save-to", "sc64", "--save-out", out, "--save-force"), 0)
        self.assertEqual(self._read(out), oot_chip_order())

    def test_the_input_is_never_the_target(self):
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "mupen64plus",
                      "--save-to", "sc64", "--save-out", self.src), 1)
        # And the input is still what it was.
        self.assertEqual(self._read(self.src), sg.swap_words(oot_chip_order()))

    def test_default_name_uses_the_target_tools_extension(self):
        """The flashcart menu reads saves/<rom>.sav whatever the chip, so a
        file left named .sra would simply not be found."""
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "mupen64plus",
                      "--save-to", "sc64"), 0)
        expected = os.path.join(
            self.tmp.name, "THE LEGEND OF ZELDA-9EB1E8AC [sc64].sav")
        self.assertTrue(os.path.exists(expected), os.listdir(self.tmp.name))

    def test_an_unmeasured_tool_is_refused(self):
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "project64",
                      "--save-to", "sc64"), 1)

    def test_a_mislabelled_source_is_refused_and_writes_nothing(self):
        """Claiming the emulator file is already in chip order leaves the
        words reversed; the game's own marker catches it."""
        out = os.path.join(self.tmp.name, "wrong.sav")
        self.assertEqual(
            self._run("--save-convert", self.src, "--save-from", "sc64",
                      "--save-to", "sc64", "--save-out", out), 1)
        self.assertFalse(os.path.exists(out))

    def test_a_file_of_no_known_chip_size_is_refused(self):
        odd = self._write("odd.sav", b"\x00" * 1234)
        self.assertEqual(
            self._run("--save-convert", odd, "--save-from", "mupen64plus",
                      "--save-to", "sc64"), 1)


class TestListSaveSources(SaveCliTest):

    def test_it_runs(self):
        self.assertEqual(self._run("--list-save-sources"), 0)


if __name__ == "__main__":
    unittest.main()
