"""Unit tests for the save file engine.

Everything here runs on synthetic data: the byte order transformations are
provable on their own (each is its own inverse), and the detection
heuristic is exercised with text that stands in for what real saves carry.
Behaviour against actual emulator and flashcart files is a separate,
calibrated layer - these tests deliberately do not pretend to cover it.
"""

import unittest

from n64patcher import savegame as sg


def oot_save(slots=2):
    """A synthetic Ocarina of Time SRAM save: the fixed header the game
    always writes, plus a marker for each save slot the player created."""
    blob = bytearray(b"\xFF" * (32 * 1024))
    blob[:len(sg.OOT_HEADER)] = sg.OOT_HEADER
    for off in sg.OOT_MARKER_OFFSETS[:slots]:
        blob[off:off + len(sg.OOT_MARKER)] = sg.OOT_MARKER
    return bytes(blob)


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

    def test_every_chip_type_erases_to_ff(self):
        """Measured on a real card: all 40 never-written saves across all
        five chip types read as 0xFF, EEPROM included - where 0x00 would
        have been the natural guess. Padding with zeroes would write a
        block the game cannot tell from real data."""
        for kind in sg.SAVE_KINDS:
            self.assertEqual(kind.erased, 0xFF, kind.key)

    def test_eeprom_pads_with_ff(self):
        kind = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        out = sg.normalize_size(b"\x01", kind)
        self.assertEqual(set(out[1:]), {0xFF})

    def test_trailing_erased_padding_is_trimmed(self):
        kind = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        data = b"\x01" * kind.size + b"\xFF" * 64
        self.assertEqual(sg.normalize_size(data, kind), b"\x01" * kind.size)

    def test_trimming_real_data_is_refused(self):
        """Silently dropping the tail is how a save loses a slot. The size
        mismatch usually means the chip type was guessed wrong."""
        kind = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        data = b"\x01" * kind.size + b"\x02" * 16
        with self.assertRaises(sg.SaveError) as ctx:
            sg.normalize_size(data, kind)
        self.assertIn("refusing to discard", str(ctx.exception))


class TestSources(unittest.TestCase):

    def test_the_flashcart_writes_chip_order_for_every_chip(self):
        for kind in sg.SAVE_KINDS:
            self.assertEqual(sg.order_for_source("sc64", kind), sg.ORDER_RAW,
                             kind.key)

    def test_one_tool_can_disagree_with_itself(self):
        """The measurement that shaped this whole model: mupen64plus writes
        EEPROM in chip order but reverses the 32-bit words of everything on
        the cartridge bus. A single order per tool would have carried the
        EEPROM result over and scrambled every SRAM and FlashRAM save."""
        expected = {
            sg.EEPROM_4K: sg.ORDER_RAW,
            sg.EEPROM_16K: sg.ORDER_RAW,
            sg.SRAM_256K: sg.ORDER_WORD,
            sg.FLASHRAM_1M: sg.ORDER_WORD,
        }
        for kind_key, order in expected.items():
            kind = sg.SAVE_KINDS_BY_KEY[kind_key]
            self.assertEqual(sg.order_for_source("mupen64plus", kind), order,
                             kind_key)

    def test_an_unmeasured_chip_type_is_refused_not_inferred(self):
        pak = sg.SAVE_KINDS_BY_KEY[sg.CONTROLLER_PAK]
        with self.assertRaises(sg.SaveError) as ctx:
            sg.order_for_source("mupen64plus", pak)
        self.assertIn("unknown", str(ctx.exception))

    def test_every_shipped_source_carries_its_evidence(self):
        """An entry without a measurement behind it is a guess, and a guess
        here corrupts saves silently."""
        for source in sg.SOURCES:
            self.assertTrue(source.evidence.strip(), source.key)
            self.assertTrue(source.orders, source.key)
            for kind_key, order in source.orders.items():
                self.assertIn(kind_key, sg.SAVE_KINDS_BY_KEY, source.key)
                self.assertIn(order, sg.ORDERS, source.key)

    def test_an_unmeasured_source_is_refused_not_assumed(self):
        with self.assertRaises(sg.SaveError) as ctx:
            sg.order_for_source("some-emulator", sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K])
        self.assertIn("no measured byte order", str(ctx.exception))


class TestOcarinaOfTimeCheck(unittest.TestCase):

    def _save(self, slots=2):
        return oot_save(slots)

    def test_the_header_and_both_slots_are_checked(self):
        result = sg.check_oot(self._save())
        self.assertTrue(result.ok)
        self.assertEqual(result.valid, 3)   # header + two slots

    def test_a_save_with_no_slots_yet_is_valid_not_broken(self):
        """Two of the six real saves examined were exactly this: the game
        had initialised the chip but the player had created no file.
        Reading them as damaged was this checker's first mistake."""
        result = sg.check_oot(self._save(slots=0))
        self.assertTrue(result.ok)
        self.assertEqual(result.valid, 1)   # the header alone
        self.assertEqual(result.unused, 2)

    def test_a_foreign_file_of_the_right_size_is_rejected(self):
        self.assertFalse(sg.check_oot(bytes(range(256)) * 128).ok)

    def test_the_result_says_marker_not_checksum(self):
        """A placement check is weaker than a checksum and must not be
        reported as one."""
        self.assertEqual(sg.check_oot(self._save()).basis, "marker")
        self.assertIn("marker", sg.check_oot(self._save()).describe())

    def test_a_swapped_save_is_rejected(self):
        self.assertFalse(sg.check_oot(sg.swap_words(self._save())).ok)

    def test_an_untouched_chip_is_empty_not_broken(self):
        result = sg.check_oot(b"\xFF" * (32 * 1024))
        self.assertEqual((result.valid, result.invalid), (0, 0))


class TestFlashRamGames(unittest.TestCase):
    """The two FlashRAM titles that settled that chip's byte order."""

    def _mm(self, copies=2):
        blob = bytearray(b"\xFF" * (128 * 1024))
        for off in sg.MM_MARKER_OFFSETS[:copies]:
            blob[off:off + len(sg.MM_MARKER)] = sg.MM_MARKER
        return bytes(blob)

    def _pm(self):
        blob = bytearray(b"\xFF" * (128 * 1024))
        blob[:len(sg.PM_MARKER)] = sg.PM_MARKER
        return bytes(blob)

    def test_majoras_mask_marker_and_its_copy(self):
        result = sg.check_mm(self._mm())
        self.assertTrue(result.ok)
        self.assertEqual(result.valid, 2)

    def test_one_copy_is_enough_to_prove_the_layout(self):
        """The second copy is not always populated; a missing one is unused,
        not wrong."""
        result = sg.check_mm(self._mm(copies=1))
        self.assertTrue(result.ok)
        self.assertEqual(result.unused, 1)

    def test_paper_mario_marker(self):
        self.assertTrue(sg.check_paper_mario(self._pm()).ok)

    def test_both_reject_a_word_swapped_file(self):
        """Which is exactly how the emulator's files were caught."""
        self.assertFalse(sg.check_mm(sg.swap_words(self._mm())).ok)
        self.assertFalse(sg.check_paper_mario(sg.swap_words(self._pm())).ok)

    def test_both_reject_the_wrong_size(self):
        for checker in (sg.check_mm, sg.check_paper_mario):
            with self.assertRaises(sg.SaveError):
                checker(b"\xFF" * (32 * 1024))

    def test_an_untouched_chip_is_empty_not_broken(self):
        for checker in (sg.check_mm, sg.check_paper_mario):
            result = checker(b"\xFF" * (128 * 1024))
            self.assertEqual((result.valid, result.invalid), (0, 0))

    def test_flashram_conversion_is_verified_end_to_end(self):
        kind = sg.SAVE_KINDS_BY_KEY[sg.FLASHRAM_1M]
        emulator_file = sg.swap_words(self._mm())
        result = sg.convert_save(emulator_file, kind, "mupen64plus", "sc64",
                                 game="The Legend of Zelda: Majora's Mask")
        self.assertTrue(result.changed)
        self.assertEqual(result.data, self._mm())
        self.assertTrue(result.check.ok)


class TestIdentifyGame(unittest.TestCase):

    def _oot(self, order=sg.ORDER_RAW):
        return sg.reorder(oot_save(), order)

    SRAM = property(lambda self: sg.SAVE_KINDS_BY_KEY[sg.SRAM_256K])

    def test_contents_beat_the_file_name(self):
        """A renamed save must still be recognised. Name matching alone
        loses the validation exactly when files are being moved around,
        which is when it is needed."""
        self.assertEqual(sg.identify_game("/x/backup-01.bin", self.SRAM, self._oot()),
                         "The Legend of Zelda: Ocarina of Time")

    def test_recognised_even_while_still_in_the_source_order(self):
        """Identification happens before conversion, so the file is still
        arranged the way the emulator wrote it."""
        self.assertEqual(
            sg.identify_game("/x/anything.sra", self.SRAM, self._oot(sg.ORDER_WORD)),
            "The Legend of Zelda: Ocarina of Time")

    def test_the_name_still_helps_when_contents_do_not(self):
        empty = b"\xFF" * (32 * 1024)
        self.assertEqual(
            sg.identify_game("/x/Ocarina of Time.sav", self.SRAM, empty),
            "The Legend of Zelda: Ocarina of Time")

    def test_a_game_is_only_considered_for_its_own_chip(self):
        """Otherwise a 32 KiB file would match both SRAM and Controller Pak
        candidates and the size ambiguity could never be resolved."""
        pak = sg.SAVE_KINDS_BY_KEY[sg.CONTROLLER_PAK]
        self.assertIsNone(sg.identify_game("/x/zelda.mpk", pak, self._oot()))

    def test_unknown_data_yields_nothing(self):
        self.assertIsNone(sg.identify_game("/x/mystery.sra", self.SRAM,
                                           bytes(range(256)) * 128))


class TestKindForFile(unittest.TestCase):

    def test_a_unique_size_is_enough(self):
        self.assertEqual(sg.kind_for_file("/x/a.bin", b"\x00" * 512).key,
                         sg.EEPROM_4K)

    def test_the_extension_settles_an_ambiguous_size(self):
        self.assertEqual(sg.kind_for_file("/x/a.sra", b"\x00" * 32768).key,
                         sg.SRAM_256K)

    def test_contents_settle_what_the_flashcart_naming_cannot(self):
        """Every save on the card is called .sav whatever the chip, so the
        commonest real file has no extension to go on."""
        self.assertEqual(sg.kind_for_file("/x/game.sav", oot_save()).key,
                         sg.SRAM_256K)

    def test_a_genuinely_ambiguous_file_is_refused(self):
        with self.assertRaises(sg.SaveError) as ctx:
            sg.kind_for_file("/x/game.sav", bytes(range(256)) * 128)
        self.assertIn("--save-type", str(ctx.exception))

    def test_an_explicit_type_wins(self):
        self.assertEqual(
            sg.kind_for_file("/x/game.sav", b"\x00" * 32768,
                             requested=sg.CONTROLLER_PAK).key,
            sg.CONTROLLER_PAK)

    def test_a_size_no_chip_has_is_refused(self):
        with self.assertRaises(sg.SaveError) as ctx:
            sg.kind_for_file("/x/game.sav", b"\x00" * 1234)
        self.assertIn("matches no N64 save chip", str(ctx.exception))


class TestConvertSave(unittest.TestCase):
    """End to end, on the pairing that actually needs converting."""

    SRAM = property(lambda self: sg.SAVE_KINDS_BY_KEY[sg.SRAM_256K])
    OOT = "The Legend of Zelda: Ocarina of Time"

    def _chip_order_save(self):
        return oot_save()

    def test_emulator_sram_to_flashcart_swaps_the_words(self):
        emulator_file = sg.swap_words(self._chip_order_save())
        result = sg.convert_save(emulator_file, self.SRAM, "mupen64plus",
                                 "sc64", game=self.OOT)
        self.assertTrue(result.changed)
        self.assertEqual(result.data, self._chip_order_save())
        self.assertTrue(result.check.ok)

    def test_the_conversion_is_reversible(self):
        emulator_file = sg.swap_words(self._chip_order_save())
        to_cart = sg.convert_save(emulator_file, self.SRAM, "mupen64plus", "sc64")
        back = sg.convert_save(to_cart.data, self.SRAM, "sc64", "mupen64plus")
        self.assertEqual(back.data, emulator_file)

    def test_a_pairing_that_needs_nothing_says_so(self):
        eeprom = sg.SAVE_KINDS_BY_KEY[sg.EEPROM_4K]
        data = bytes(range(256)) * 2
        result = sg.convert_save(data, eeprom, "mupen64plus", "sc64")
        self.assertFalse(result.changed)
        self.assertEqual(result.data, data)
        self.assertIn("no bytes changed", result.describe())

    def test_a_mislabelled_source_is_caught_by_the_game_itself(self):
        """Claiming an emulator file is already in chip order leaves the
        words reversed. Without the game's own check nothing would notice
        until the save failed on the console."""
        emulator_file = sg.swap_words(self._chip_order_save())
        with self.assertRaises(sg.SaveError) as ctx:
            sg.convert_save(emulator_file, self.SRAM, "sc64", "sc64",
                            game=self.OOT)
        self.assertIn("Refusing to hand back", str(ctx.exception))

    def test_an_unknown_game_converts_without_a_verdict(self):
        emulator_file = sg.swap_words(self._chip_order_save())
        result = sg.convert_save(emulator_file, self.SRAM, "mupen64plus", "sc64",
                                 game="Some Game With No Profile")
        self.assertIsNone(result.check)
        self.assertTrue(result.changed)


class TestSuperMario64Check(unittest.TestCase):
    """The checksum rule was brute-forced from a real save and confirmed on
    six of them, European and American, 10 blocks of 10 each."""

    def _block(self, size, payload):
        body = bytearray(payload[:size - 2].ljust(size - 2, b"\x00"))
        body += (sum(body) & 0xFFFF).to_bytes(2, "big")
        return bytes(body)

    def _save(self, written_slots=4):
        out = bytearray()
        for i in range(8):
            if i < written_slots:
                out += self._block(sg.SM64_SLOT_SIZE, b"\x00\x00\x3c\x01" + bytes([i]))
            else:
                out += b"\xFF" * sg.SM64_SLOT_SIZE
        for _ in range(2):
            out += self._block(sg.SM64_MENU_SIZE, b"\x44\x41")
        return bytes(out)

    def test_a_well_formed_save_passes(self):
        result = sg.check_sm64(self._save())
        self.assertTrue(result.ok)
        self.assertEqual(result.valid, 6)   # 4 slots + 2 menu blocks
        self.assertEqual(result.invalid, 0)

    def test_the_layout_fills_the_chip_exactly(self):
        self.assertEqual(len(self._save()), 512)
        self.assertEqual(
            len(sg.SM64_SLOT_BLOCKS) * sg.SM64_SLOT_SIZE
            + len(sg.SM64_MENU_BLOCKS) * sg.SM64_MENU_SIZE, 512)

    def test_a_flipped_byte_is_caught(self):
        data = bytearray(self._save())
        data[4] ^= 0xFF
        self.assertFalse(sg.check_sm64(bytes(data)).ok)

    def test_an_erased_chip_reports_empty_rather_than_broken(self):
        result = sg.check_sm64(b"\xFF" * 512)
        self.assertEqual((result.valid, result.invalid), (0, 0))
        self.assertIn("empty save", result.describe())

    def test_a_wrongly_swapped_save_fails_its_own_checksum(self):
        """This is what makes a conversion provable instead of hopeful: the
        game's checksum only holds in the correct byte order."""
        good = self._save()
        self.assertTrue(sg.check_sm64(good).ok)
        swapped = sg.reorder(good, sg.ORDER_WORD)
        self.assertFalse(sg.check_sm64(swapped).ok)
        self.assertTrue(sg.check_sm64(sg.reorder(swapped, sg.ORDER_WORD)).ok)

    def test_a_wrong_size_is_rejected(self):
        with self.assertRaises(sg.SaveError):
            sg.check_sm64(b"\xFF" * 2048)


class TestRegion(unittest.TestCase):

    def test_region_note_does_not_promise_a_conversion(self):
        """Until a game has a profile, the tool must not imply that it can
        transform a save between regions - for most titles there is nothing
        to transform."""
        note = sg.region_note()
        self.assertIn("identical across PAL and NTSC", note)


if __name__ == "__main__":
    unittest.main()
