"""Unit tests for the save file engine.

Everything here runs on synthetic data: the byte order transformations are
provable on their own (each is its own inverse), and the detection
heuristic is exercised with text that stands in for what real saves carry.
Behaviour against actual emulator and flashcart files is a separate,
calibrated layer - these tests deliberately do not pretend to cover it.
"""

import unittest

from n64patcher import savegame as sg


class TestSaveKinds(unittest.TestCase):

    def test_sizes_are_the_hardware_sizes(self):
        expected = {
            sg.EEPROM_4K: 512,
            sg.EEPROM_16K: 2048,
            sg.SRAM_256K: 32768,
            sg.SRAM_768K: 98304,
            sg.FLASHRAM_1M: 131072,
            sg.CONTROLLER_PAK: 32768,
        }
        for key, size in expected.items():
            self.assertEqual(sg.SAVE_KINDS_BY_KEY[key].size, size, key)

    def test_size_alone_does_not_always_identify_a_chip(self):
        """SRAM and a Controller Pak are both 32 KiB. Anything that claims a
        unique answer from the size is wrong, so the API returns a list."""
        by_size = sg.kinds_for_size(32 * 1024)
        self.assertEqual({k.key for k in by_size}, {sg.SRAM_256K, sg.CONTROLLER_PAK})

    def test_unknown_size_matches_nothing(self):
        self.assertEqual(sg.kinds_for_size(1234), [])

    def test_extension_lookup(self):
        self.assertEqual([k.key for k in sg.kinds_for_extension("/x/game.fla")],
                         [sg.FLASHRAM_1M])
        self.assertEqual({k.key for k in sg.kinds_for_extension("/x/GAME.EEP")},
                         {sg.EEPROM_4K, sg.EEPROM_16K})
        self.assertEqual(sg.kinds_for_extension("/x/game.z64"), [])


class TestByteOrder(unittest.TestCase):

    SAMPLE = bytes(range(64))

    def test_word_swap_reverses_groups_of_four(self):
        self.assertEqual(sg.swap_words(b"\x01\x02\x03\x04"), b"\x04\x03\x02\x01")

    def test_halfword_swap_reverses_pairs(self):
        self.assertEqual(sg.swap_halfwords(b"\x01\x02\x03\x04"), b"\x02\x01\x04\x03")

    def test_every_order_is_its_own_inverse(self):
        for order in sg.ORDERS:
            once = sg.reorder(self.SAMPLE, order)
            self.assertEqual(sg.reorder(once, order), self.SAMPLE, order)

    def test_swaps_never_change_the_length(self):
        for length in range(0, 12):
            data = bytes(range(length))
            self.assertEqual(len(sg.swap_words(data)), length)
            self.assertEqual(len(sg.swap_halfwords(data)), length)

    def test_trailing_partial_group_is_left_alone(self):
        """A file whose length is not a multiple of 4 is already suspect;
        padding it to complete a group would change its size."""
        self.assertEqual(sg.swap_words(b"\x01\x02\x03\x04\xAA\xBB"),
                         b"\x04\x03\x02\x01\xAA\xBB")
        self.assertEqual(sg.swap_halfwords(b"\x01\x02\x03"), b"\x02\x01\x03")

    def test_convert_between_two_non_raw_orders(self):
        original = self.SAMPLE
        as_word = sg.reorder(original, sg.ORDER_WORD)
        as_half = sg.convert_order(as_word, sg.ORDER_WORD, sg.ORDER_HALF)
        self.assertEqual(as_half, sg.reorder(original, sg.ORDER_HALF))
        # ... and back again
        self.assertEqual(
            sg.convert_order(as_half, sg.ORDER_HALF, sg.ORDER_WORD), as_word)

    def test_converting_to_the_same_order_is_a_copy(self):
        self.assertEqual(sg.convert_order(self.SAMPLE, sg.ORDER_WORD,
                                          sg.ORDER_WORD), self.SAMPLE)

    def test_unknown_order_is_rejected(self):
        with self.assertRaises(ValueError):
            sg.reorder(self.SAMPLE, "middle-endian")
        with self.assertRaises(ValueError):
            sg.convert_order(self.SAMPLE, sg.ORDER_RAW, "nonsense")


class TestStructureScore(unittest.TestCase):

    def test_big_endian_small_integers_score_positive(self):
        self.assertGreater(sg.structure_score(b"\x00\x00\x00\x01"), 0)

    def test_the_same_words_reversed_score_negative(self):
        self.assertLess(sg.structure_score(b"\x01\x00\x00\x00"), 0)

    def test_empty_and_erased_groups_are_ignored(self):
        """Zeroed structures and erased chip read identically in every
        arrangement, so counting them would only add noise."""
        self.assertEqual(sg.structure_score(b"\x00" * 64), 0)
        self.assertEqual(sg.structure_score(b"\xFF" * 64), 0)

    def test_a_trailing_partial_group_is_ignored(self):
        self.assertEqual(sg.structure_score(b"\x00\x00\x01"), 0)


class TestOrderDetection(unittest.TestCase):

    def _save(self):
        """Stand-in for a real save: erased chip, a few player-facing
        strings, and the counters and offsets that carry the byte order."""
        blob = bytearray(512)
        blob[0x10:0x18] = b"MARIO   "
        blob[0x40:0x45] = b"EMPTY"
        for i in range(16):
            # small big-endian values: star counts, flags, offsets
            blob[0x100 + i * 4:0x104 + i * 4] = (i + 1).to_bytes(4, "big")
        return bytes(blob)

    def test_printable_text_carries_no_order_information(self):
        """Guard against 'improving' the detector with a text heuristic:
        byte-swapping ASCII produces more ASCII, so any measure of how
        printable the bytes are scores every arrangement the same."""
        text = b"MARIOLINKKIRBYFOX!"
        printable = sum(1 for b in text if 0x20 <= b <= 0x7E)
        for order in sg.ORDERS:
            swapped = sg.reorder(text, order)
            self.assertEqual(sum(1 for b in swapped if 0x20 <= b <= 0x7E),
                             printable, order)

    def test_chip_order_is_recognised(self):
        guess = sg.detect_order(self._save())
        self.assertTrue(guess.decided, guess.reason)
        self.assertEqual(guess.order, sg.ORDER_RAW)

    def test_word_swapped_file_is_recognised(self):
        swapped = sg.swap_words(self._save())
        guess = sg.detect_order(swapped)
        self.assertEqual(guess.order, sg.ORDER_WORD, guess.reason)
        # The detected order converts it back to what we started from.
        self.assertEqual(sg.reorder(swapped, guess.order), self._save())

    def test_halfword_swapped_file_is_recognised(self):
        swapped = sg.swap_halfwords(self._save())
        guess = sg.detect_order(swapped)
        self.assertEqual(guess.order, sg.ORDER_HALF, guess.reason)
        self.assertEqual(sg.reorder(swapped, guess.order), self._save())

    def test_an_empty_save_is_reported_as_undecided(self):
        """The honest answer for a save with no signal. Returning 'raw'
        here would look like an answer and mangle half the inputs."""
        guess = sg.detect_order(bytes(512))
        self.assertFalse(guess.decided)
        self.assertIsNone(guess.order)
        self.assertIn("no signal", guess.reason)

    def test_symmetric_data_carries_no_signal(self):
        """Words that read the same either way vote for nothing."""
        guess = sg.detect_order(b"\x01\x00\x00\x01" * 32)
        self.assertFalse(guess.decided)
        self.assertIn("no signal", guess.reason)

    def test_a_lead_too_small_to_trust_is_reported_as_undecided(self):
        """One big-endian-looking word among symmetric ones puts chip order
        marginally ahead. Marginally is not enough - a wrong guess here
        scrambles every byte of the save."""
        data = b"\x00\x00\x00\x01" + b"\x01\x00\x00\x01" * 32
        guess = sg.detect_order(data)
        self.assertEqual(max(guess.scores.values()), guess.scores[sg.ORDER_RAW])
        self.assertFalse(guess.decided)
        self.assertIn("score alike", guess.reason)

    def test_scores_are_reported_for_every_order(self):
        guess = sg.detect_order(self._save())
        self.assertEqual(set(guess.scores), set(sg.ORDERS))


class TestNormalizeSize(unittest.TestCase):

    def test_exact_size_is_untouched(self):
        kind = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        data = bytes(range(256)) * 2
        self.assertEqual(sg.normalize_size(data, kind), data)

    def test_short_file_is_padded_with_the_erased_value(self):
        kind = sg.SAVE_KINDS_BY_KEY[sg.FLASHRAM_1M]
        out = sg.normalize_size(b"\x01\x02", kind)
        self.assertEqual(len(out), kind.size)
        self.assertEqual(out[:2], b"\x01\x02")
        self.assertEqual(set(out[2:]), {0xFF})

    def test_eeprom_pads_with_zero_not_ff(self):
        kind = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        out = sg.normalize_size(b"\x01", kind)
        self.assertEqual(set(out[1:]), {0x00})

    def test_trailing_erased_padding_is_trimmed(self):
        kind = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        data = b"\x01" * kind.size + b"\x00" * 64
        self.assertEqual(sg.normalize_size(data, kind), b"\x01" * kind.size)

    def test_trimming_real_data_is_refused(self):
        """Silently dropping the tail is how a save loses a slot. The size
        mismatch usually means the chip type was guessed wrong."""
        kind = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        data = b"\x01" * kind.size + b"\x02" * 16
        with self.assertRaises(sg.SaveError) as ctx:
            sg.normalize_size(data, kind)
        self.assertIn("refusing to discard", str(ctx.exception))


class TestRegion(unittest.TestCase):

    def test_region_note_does_not_promise_a_conversion(self):
        """Until a game has a profile, the tool must not imply that it can
        transform a save between regions - for most titles there is nothing
        to transform."""
        note = sg.region_note()
        self.assertIn("identical across PAL and NTSC", note)


if __name__ == "__main__":
    unittest.main()
