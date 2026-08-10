"""N64 save file handling: identification, byte order, safe conversion.

A save is whatever the game's own code wrote into the cartridge's save
chip. That has two consequences this module is built around.

**There is no region field.** A PAL and an NTSC dump of the same game
usually run the same save code, so the data is byte-identical in layout
and the save simply works on the other version. Where it does not, the
difference is specific to that one game, and no transformation derivable
from the file itself can fix it. "Convert PAL to NTSC" is therefore not
an operation this module offers; per-game knowledge belongs in a profile
(see `region_note`), and the honest answer for most titles is that
nothing needs converting.

**What genuinely differs between tools is the byte order.** Emulators and
flashcarts disagree about how to lay the chip contents out in a file, so
the same save appears with its 32-bit words or 16-bit halfwords
byte-reversed depending on where it came from. That is a well-defined,
reversible transformation and it is the usual reason a save "does not
work" after moving it.

Everything here refuses rather than guesses: an inconclusive detection
returns no answer, and a size change that would discard real bytes is an
error, not a silent truncation.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass


class SaveError(ValueError):
    """A save file cannot be handled as asked."""


# ---------------------------------------------------------------------------
# Save chip types
#
# The sizes are fixed by the hardware, and flashcarts expect the file to be
# exactly that long - a short or padded file is a common reason a save is
# ignored on real hardware.
# ---------------------------------------------------------------------------

EEPROM_4K = "eeprom4k"
EEPROM_16K = "eeprom16k"
SRAM_256K = "sram256k"
SRAM_768K = "sram768k"
FLASHRAM_1M = "flashram"
CONTROLLER_PAK = "mempak"


#: What an untouched chip reads as.
#:
#: Measured, not assumed: across a 132-file SummerCart64 card, every one of
#: the 40 saves that had never been written was filled with 0xFF, and that
#: held for all five chip types - EEPROM included, where 0x00 would have
#: been the natural guess. Padding a short file with zeroes would write a
#: block the game cannot tell from real data.
ERASED = 0xFF


@dataclass(frozen=True)
class SaveKind:
    key: str
    label: str
    size: int
    extensions: tuple[str, ...]
    erased: int = ERASED


SAVE_KINDS: tuple[SaveKind, ...] = (
    SaveKind(EEPROM_4K, "EEPROM 4 Kbit", 512, (".eep",)),
    SaveKind(EEPROM_16K, "EEPROM 16 Kbit", 2 * 1024, (".eep",)),
    SaveKind(SRAM_256K, "SRAM 256 Kbit", 32 * 1024, (".sra", ".srm")),
    SaveKind(SRAM_768K, "SRAM 768 Kbit", 96 * 1024, (".sra", ".srm")),
    SaveKind(FLASHRAM_1M, "FlashRAM 1 Mbit", 128 * 1024, (".fla", ".flash")),
    SaveKind(CONTROLLER_PAK, "Controller Pak", 32 * 1024, (".mpk", ".pak")),
)

SAVE_KINDS_BY_KEY = {k.key: k for k in SAVE_KINDS}


def kinds_for_size(size: int) -> list[SaveKind]:
    """Every chip type that is exactly *size* bytes.

    Not unique on purpose: SRAM 256 Kbit and a Controller Pak are both
    32 KiB, and telling them apart needs the contents or the extension.
    """
    return [k for k in SAVE_KINDS if k.size == size]


def kinds_for_extension(path: str) -> list[SaveKind]:
    ext = os.path.splitext(path)[1].lower()
    return [k for k in SAVE_KINDS if ext in k.extensions]


# ---------------------------------------------------------------------------
# Byte order
# ---------------------------------------------------------------------------

ORDER_RAW = "raw"        # chip order: what hardware and flashcarts expect
ORDER_WORD = "word"      # 32-bit words byte-reversed
ORDER_HALF = "half"      # 16-bit halfwords byte-reversed

ORDERS = (ORDER_RAW, ORDER_WORD, ORDER_HALF)

ORDER_LABELS = {
    ORDER_RAW: "chip order (hardware / flashcart)",
    ORDER_WORD: "32-bit words byte-reversed",
    ORDER_HALF: "16-bit halfwords byte-reversed",
}


def swap_words(data: bytes) -> bytes:
    """Reverse each aligned group of 4 bytes.

    A trailing partial group is passed through untouched rather than
    padded: inventing bytes to complete a group would change the file
    length, and a save whose length is not a multiple of 4 is already
    suspect enough to report rather than repair.
    """
    n = len(data) - (len(data) % 4)
    out = bytearray(data)
    out[0:n:4], out[1:n:4], out[2:n:4], out[3:n:4] = (
        bytes(out[3:n:4]), bytes(out[2:n:4]), bytes(out[1:n:4]), bytes(out[0:n:4]))
    return bytes(out)


def swap_halfwords(data: bytes) -> bytes:
    """Reverse each aligned pair of bytes."""
    n = len(data) - (len(data) % 2)
    out = bytearray(data)
    out[0:n:2], out[1:n:2] = bytes(out[1:n:2]), bytes(out[0:n:2])
    return bytes(out)


def reorder(data: bytes, order: str) -> bytes:
    """Apply a byte order transformation. Every one is its own inverse, so
    the same call converts in both directions."""
    if order == ORDER_RAW:
        return bytes(data)
    if order == ORDER_WORD:
        return swap_words(data)
    if order == ORDER_HALF:
        return swap_halfwords(data)
    raise ValueError(f"unknown byte order: {order!r}")


def convert_order(data: bytes, source: str, target: str) -> bytes:
    """Rewrite *data* from byte order *source* into byte order *target*.

    Both are expressed relative to chip order, so converting between two
    non-raw orders means undoing one and applying the other.
    """
    for name in (source, target):
        if name not in ORDERS:
            raise ValueError(f"unknown byte order: {name!r}")
    if source == target:
        return bytes(data)
    return reorder(reorder(data, source), target)


# ---------------------------------------------------------------------------
# Byte order detection
#
# Saves are game-defined blobs with no header to read, so this is
# inference, and the metric has to be chosen with care.
#
# Readable text is the obvious idea and it does not work: byte-swapping
# ASCII yields more ASCII. "MARI" becomes "IRAM" - scrambled to a reader,
# identical to any measure of how printable the bytes are. Every
# arrangement scores the same and the detector learns nothing.
#
# What does carry the order is how integers sit in memory. The N64 is
# big-endian, so the counters, offsets and small flags that fill a save
# are stored with their zero bytes first: 00 00 00 01. Reverse the words
# and those zeros move to the back: 01 00 00 00. Leading zeros minus
# trailing zeros over every non-empty word is therefore a signed vote for
# the arrangement, and it is a property of the encoding rather than of any
# one game.
#
# It stays a hint. Where the caller knows the source, it should say so
# instead; where the game is known, a signature at a known offset settles
# it outright. When the votes do not separate, the answer is "unknown" -
# never a default.
# ---------------------------------------------------------------------------

# MEASURED ACCURACY - read before relying on this.
#
# Scored against a 132-file SummerCart64 card where the true order is
# known (the flashcart writes chip order; "ZELDAZ" stands unscrambled at
# 0x3c in the Ocarina of Time save, and its backup copy at 0x3d2c):
#
#     correct    29 / 90   (32 %)
#     WRONG      18 / 90   (20 %)
#     undecided  43 / 90   (47 %)
#
# One save in five is called backwards. The failures are not random: they
# are the games whose saves are full of floating-point data - F-Zero X
# with 962 float-shaped words against 2 small integers, plus Mario Kart
# 64, Mario Tennis, GoldenEye, AeroFighters. A big-endian float carries
# its zero bytes at the *end* (1.0 is 3F 80 00 00), which is precisely the
# pattern this metric reads as reversed. Ghost and replay data therefore
# votes against the truth, consistently and strongly.
#
# So this function is a hint for a human to look at, never the input to a
# conversion. The byte order is a property of the tool that wrote the file
# (see SOURCES) and that is what callers must go by.

#: The winner must beat the runner-up by this margin, in votes, to count.
#: An absolute floor rather than a ratio: scores can be zero or negative.
_DECISION_MARGIN = 8


def structure_score(data: bytes) -> int:
    """How much *data* looks like big-endian words.

    Sums, over each aligned 4-byte group, the leading zero bytes minus the
    trailing zero bytes. Groups that are entirely 0x00 or 0xFF are skipped:
    erased chip and zeroed structures read the same in every arrangement
    and would only add noise.
    """
    score = 0
    for i in range(0, len(data) - 3, 4):
        group = data[i:i + 4]
        if group == b"\x00\x00\x00\x00" or group == b"\xFF\xFF\xFF\xFF":
            continue
        leading = 0
        for byte in group:
            if byte:
                break
            leading += 1
        trailing = 0
        for byte in reversed(group):
            if byte:
                break
            trailing += 1
        score += leading - trailing
    return score


@dataclass(frozen=True)
class OrderGuess:
    """Outcome of byte order detection.

    `order` is None when the evidence does not decide it. Callers must not
    treat None as "raw" - they have to ask, or be told by the user.
    """
    order: str | None
    scores: dict[str, int]
    reason: str

    @property
    def decided(self) -> bool:
        return self.order is not None


def detect_order(data: bytes) -> OrderGuess:
    """Guess which byte order *data* is stored in.

    A hint for display, not a verdict to act on - it is wrong on one save
    in five (see the note above this function). Use `order_for_source`
    when the origin of the file is known, which is the normal case.
    """
    scores = {name: structure_score(reorder(data, name)) for name in ORDERS}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1]

    if best_score <= 0:
        return OrderGuess(None, scores,
                          "no arrangement looks like big-endian data - this "
                          "save carries no signal to detect the order from")
    if best_score - runner_up < _DECISION_MARGIN:
        return OrderGuess(None, scores,
                          "several arrangements score alike; the source has "
                          "to be stated rather than guessed")
    return OrderGuess(best, scores,
                      f"reads as big-endian data in {ORDER_LABELS[best]}")


# ---------------------------------------------------------------------------
# Where a save came from
#
# The byte order is a property of the tool that wrote the file, not of the
# file itself, so the reliable way to convert is to name the source and
# the destination. Each entry here is a measurement or it is not made:
# guessing at an emulator's convention from memory is how a converter
# quietly corrupts saves.
# ---------------------------------------------------------------------------

ALL_KINDS = tuple(k.key for k in SAVE_KINDS)


@dataclass(frozen=True)
class SaveSource:
    key: str
    label: str
    #: Chip type -> byte order, one entry per type actually measured.
    #:
    #: A mapping rather than a single value because a tool is free to be
    #: inconsistent with itself, and one of them is: mupen64plus writes
    #: EEPROM in chip order and SRAM word-swapped. A single "this is what
    #: the emulator does" field would have carried the EEPROM result over
    #: to SRAM and scrambled every 32 KiB save it touched.
    orders: dict[str, str]
    #: How those orders were established. Empty means unverified, and such
    #: an entry must not ship.
    evidence: str


SOURCES: tuple[SaveSource, ...] = (
    SaveSource(
        "sc64", "SummerCart64 / N64FlashcartMenu",
        dict.fromkeys(ALL_KINDS, ORDER_RAW),
        "Measured on a 132-save card: the Ocarina of Time save carries its "
        "'ZELDAZ' marker unscrambled at 0x3c, with the backup copy at "
        "0x3d2c, so the file is in chip order. Saves of all five chip types "
        "were present and none showed a differing arrangement"),
    SaveSource(
        "mupen64plus", "mupen64plus",
        {EEPROM_4K: ORDER_RAW, EEPROM_16K: ORDER_RAW,
         SRAM_256K: ORDER_WORD, FLASHRAM_1M: ORDER_WORD},
        "Two measurements, and they disagree with each other. Its Super "
        "Mario 64 EEPROM save passes the game's own checksum exactly as "
        "stored and fails under either swap, so EEPROM is chip order. Its "
        "Ocarina of Time SRAM save holds no readable 'ZELDAZ' at all until "
        "the 32-bit words are reversed, whereupon the marker and its backup "
        "copy land at 0x3c and 0x3d2c - the same offsets the flashcart's "
        "save has them at unswapped. SRAM is therefore word-swapped, and "
        "FlashRAM behaves the same way, shown twice over: Majora's Mask "
        "puts 'ZELDA3' at 0x24 and Paper Mario 'Mario Story 006' at 0x0 "
        "only once the words are reversed, both matching the flashcart's "
        "own saves byte for byte. EEPROM being the exception fits how the "
        "hardware is reached - it hangs off the serial controller port "
        "while SRAM and FlashRAM sit on the 32-bit cartridge bus - though "
        "that is an explanation for the pattern, not something measured. "
        "Controller Pak and 768 Kbit SRAM remain unmeasured"),
    SaveSource(
        "hardware", "Real cartridge / chip dump",
        dict.fromkeys(ALL_KINDS, ORDER_RAW),
        "Chip order by definition - this is what the save chip holds"),
)

SOURCES_BY_KEY = {s.key: s for s in SOURCES}


def order_for_source(key: str, kind: SaveKind) -> str:
    """The byte order a named tool writes for a given chip type.

    Raises for anything not measured, both for an unknown tool and for a
    chip type that tool's entry does not cover. An unknown case is a
    reason to ask the user, not to assume chip order and hope.
    """
    try:
        source = SOURCES_BY_KEY[key]
    except KeyError:
        known = ", ".join(sorted(SOURCES_BY_KEY))
        raise SaveError(
            f"no measured byte order for source {key!r}. Known: {known}. "
            f"Add an entry only once it has been verified against a real "
            f"file from that tool") from None

    try:
        return source.orders[kind.key]
    except KeyError:
        covered = ", ".join(sorted(source.orders)) or "nothing"
        raise SaveError(
            f"{source.label} has only been measured for: {covered}. Its "
            f"byte order for {kind.label} is unknown, and guessing it would "
            f"risk scrambling the save - this tool writes EEPROM and SRAM "
            f"differently, so the other types cannot be inferred") from None


# ---------------------------------------------------------------------------
# Size normalisation
# ---------------------------------------------------------------------------

def normalize_size(data: bytes, kind: SaveKind) -> bytes:
    """Pad or trim *data* to the exact chip size of *kind*.

    Padding uses the erased value of that chip type. Trimming happens only
    when every byte being removed already equals that value - a tail that
    holds anything else is real data, and shortening the file would throw
    it away, so that is an error instead.
    """
    if len(data) == kind.size:
        return bytes(data)
    if len(data) < kind.size:
        return bytes(data) + bytes([kind.erased]) * (kind.size - len(data))

    tail = data[kind.size:]
    if any(b != kind.erased for b in tail):
        raise SaveError(
            f"file is {len(data)} bytes, {kind.label} holds {kind.size}, and "
            f"the extra {len(tail)} bytes are not empty - refusing to discard "
            f"them. Check that this really is a {kind.label} save")
    return bytes(data[:kind.size])


# ---------------------------------------------------------------------------
# Per-game verification
#
# A save that survives a conversion is one whose own checksum still
# passes. That check belongs to the game, so it arrives one title at a
# time, each derived from real files rather than from a description.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SaveCheck:
    """Result of validating a save against what the game itself wrote.

    `basis` names what was actually verified, because the two are not
    equally strong and reporting a marker check as a checksum would
    overstate it. A checksum covers the contents; a marker only proves
    the save is laid out the way the game writes it.
    """
    valid: int
    invalid: int
    unused: int
    basis: str = "checksum"

    @property
    def ok(self) -> bool:
        return self.invalid == 0 and self.valid > 0

    def describe(self) -> str:
        if self.valid == 0 and self.invalid == 0:
            return "empty save - nothing has been written to this chip yet"
        if self.ok:
            return f"all {self.valid} checked places match the game's {self.basis}"
        return (f"{self.invalid} of {self.valid + self.invalid} checked "
                f"places do not match the game's {self.basis}")


#: Super Mario 64, EEPROM 4 Kbit. Four save slots held twice at 56 bytes
#: each, then two 32-byte blocks of menu data, filling the chip exactly.
#: Every block ends in a 16-bit marker (0x4849 / 0x4441) and a 16-bit
#: checksum, and the checksum is the low 16 bits of the sum of the block's
#: bytes including the marker.
#:
#: Derived by brute force over a real save and confirmed on six of them,
#: European and American: 10 of 10 blocks in each.
SM64_SLOT_BLOCKS = tuple(range(0, 448, 56))
SM64_MENU_BLOCKS = tuple(range(448, 512, 32))
SM64_SLOT_SIZE = 56
SM64_MENU_SIZE = 32


def check_sm64(data: bytes) -> SaveCheck:
    """Validate a Super Mario 64 EEPROM save."""
    if len(data) != 512:
        raise SaveError(
            f"a Super Mario 64 save is 512 bytes, this one is {len(data)}")

    valid = invalid = unused = 0
    blocks = ([(off, SM64_SLOT_SIZE) for off in SM64_SLOT_BLOCKS]
              + [(off, SM64_MENU_SIZE) for off in SM64_MENU_BLOCKS])
    for off, size in blocks:
        block = data[off:off + size]
        # An erased or never-written block has nothing to verify. Both
        # fills occur in practice: 0xFF on an untouched chip, 0x00 where
        # the game cleared a slot.
        if len(set(block)) <= 1 and block[0] in (0x00, ERASED):
            unused += 1
            continue
        stored = int.from_bytes(block[-2:], "big")
        if (sum(block[:-2]) & 0xFFFF) == stored:
            valid += 1
        else:
            invalid += 1
    return SaveCheck(valid, invalid, unused)


#: Ocarina of Time opens its SRAM with a fixed 12-byte header, ending in
#: "ZELDA". Present in all six saves examined - five written by a
#: SummerCart64 and one by mupen64plus - whether or not the player has
#: created a file yet, which is what makes it the identifying mark.
OOT_HEADER = bytes.fromhex("00000098091021") + b"ZELDA"

#: Each save slot the player has created is stamped "ZELDAZ": the live
#: file and the backup the game keeps beside it. Absent on a fresh chip
#: the game has merely initialised, so a save without them is empty, not
#: broken - two of the six were exactly that, and reading them as damaged
#: was this checker's first mistake.
OOT_MARKER = b"ZELDAZ"
OOT_MARKER_OFFSETS = (0x3C, 0x3D2C)


def check_oot(data: bytes) -> SaveCheck:
    """Validate an Ocarina of Time SRAM save by its header and slots.

    This checks placement, not integrity: a matching header proves the
    save is laid out the way the game writes it - and therefore that the
    byte order is right - but says nothing about whether the contents are
    self-consistent. The game's own checksum has not been derived yet, so
    the weaker claim is the only honest one.
    """
    if len(data) != 32 * 1024:
        raise SaveError(
            f"an Ocarina of Time save is 32768 bytes, this one is {len(data)}")
    if len(set(data)) <= 1:
        return SaveCheck(0, 0, 1 + len(OOT_MARKER_OFFSETS), basis="marker")

    if data[:len(OOT_HEADER)] != OOT_HEADER:
        return SaveCheck(0, 1, 0, basis="marker")

    used = sum(1 for off in OOT_MARKER_OFFSETS
               if data[off:off + len(OOT_MARKER)] == OOT_MARKER)
    return SaveCheck(1 + used, 0, len(OOT_MARKER_OFFSETS) - used, basis="marker")


@dataclass(frozen=True)
class GameProfile:
    """What is known about one game's save."""
    name: str
    kind: str
    check: Callable[[bytes], SaveCheck]
    #: Substrings of a file name that suggest this game. A fallback only -
    #: identification goes by contents first.
    hints: tuple[str, ...]


#: Majora's Mask stamps "ZELDA3" at 0x24 and keeps a second copy at
#: 0x2024. Verified in five saves - four written by a SummerCart64, one by
#: mupen64plus - all of which carry it after merely reaching the title
#: screen, with under 200 of the 131072 bytes written.
MM_MARKER = b"ZELDA3"
MM_MARKER_OFFSETS = (0x24, 0x2024)

#: Paper Mario opens its FlashRAM with its Japanese working title.
#: Verified in two saves, one from each side.
PM_MARKER = b"Mario Story 006"
PM_MARKER_OFFSETS = (0x00,)


def _marker_check(data: bytes, size: int, label: str, marker: bytes,
                  offsets: tuple[int, ...]) -> SaveCheck:
    """Validate a save by markers the game writes at fixed offsets.

    Placement, not integrity: a match proves the layout - and so the byte
    order - is right, and says nothing about whether the contents are
    self-consistent. Where a game's checksum has not been derived, this is
    the strongest honest claim.
    """
    if len(data) != size:
        raise SaveError(f"a {label} save is {size} bytes, this one is {len(data)}")
    if len(set(data)) <= 1:
        return SaveCheck(0, 0, len(offsets), basis="marker")

    valid = sum(1 for off in offsets
                if data[off:off + len(marker)] == marker)
    # Any match proves the layout; the copies a game keeps are not all
    # populated at every moment, so a missing one is unused, not wrong.
    if valid:
        return SaveCheck(valid, 0, len(offsets) - valid, basis="marker")
    return SaveCheck(0, 1, 0, basis="marker")


def check_mm(data: bytes) -> SaveCheck:
    """Validate a Majora's Mask FlashRAM save."""
    return _marker_check(data, 128 * 1024, "Majora's Mask",
                         MM_MARKER, MM_MARKER_OFFSETS)


def check_paper_mario(data: bytes) -> SaveCheck:
    """Validate a Paper Mario FlashRAM save."""
    return _marker_check(data, 128 * 1024, "Paper Mario",
                         PM_MARKER, PM_MARKER_OFFSETS)


GAMES: tuple[GameProfile, ...] = (
    GameProfile("Super Mario 64", EEPROM_4K, check_sm64,
                ("super mario 64",)),
    GameProfile("The Legend of Zelda: Ocarina of Time", SRAM_256K, check_oot,
                ("legend of zelda", "ocarina of time")),
    GameProfile("The Legend of Zelda: Majora's Mask", FLASHRAM_1M, check_mm,
                ("majora", "mujura")),
    GameProfile("Paper Mario", FLASHRAM_1M, check_paper_mario,
                ("paper mario", "mario story")),
)

#: Games whose save can be validated. Keyed by the name used in reports.
CHECKERS = {g.name: g.check for g in GAMES}


# ---------------------------------------------------------------------------
# Recognising which game a save belongs to
#
# Both tools name a save after the ROM, which is what makes a name-based
# guess possible at all: mupen64plus uses the ROM's internal title ("SUPER
# MARIO 64-66CF018F.eep"), and the SummerCart64 menu uses the ROM's file
# name ("saves/<rom>.sav" - the rule 88 of the 132 saves on a real card
# follow). But a name survives only until someone copies the file, so it
# is the fallback and the contents come first.
# ---------------------------------------------------------------------------

def identify_game(path: str, kind: SaveKind, data: bytes | None = None) -> str | None:
    """Which game a save belongs to, or None when it cannot be told.

    Contents first, name only as a fallback. Names are the obvious hook
    and they are not good enough: copy a save to "oot-backup.sra" and a
    name-matching check quietly finds nothing, taking the validation with
    it - the safety net disappears exactly when someone has been moving
    files around, which is when it is needed.

    A game's own marker or checksum, by contrast, is in the file. It is
    looked for in every byte arrangement, because at the point of asking
    the file may still be in the source tool's order.
    """
    candidates = [g for g in GAMES if g.kind == kind.key]

    refuted = set()
    if data is not None:
        for game in candidates:
            matched = False
            for order in ORDERS:
                try:
                    if game.check(reorder(data, order)).ok:
                        return game.name
                    matched = True  # the checker ran and said no
                except SaveError:
                    break  # wrong size for this game; try the next one
            if matched and len(set(data)) > 1:
                refuted.add(game.name)

    # The name is a weaker witness and must not outvote the contents: a
    # file that carries data and matches no arrangement of a game's own
    # marks is not that game, however it happens to be called. Only an
    # empty or unreadable file falls through to the name.
    name = os.path.basename(path).lower()
    for game in candidates:
        if game.name in refuted:
            continue
        if any(needle in name for needle in game.hints):
            return game.name
    return None


# ---------------------------------------------------------------------------
# The conversion itself
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Conversion:
    """What a conversion did, in terms a user can check."""
    data: bytes
    kind: SaveKind
    source_order: str
    target_order: str
    resized_from: int | None
    check: SaveCheck | None

    @property
    def changed(self) -> bool:
        return self.source_order != self.target_order or self.resized_from is not None

    def describe(self) -> str:
        lines = [f"{self.kind.label}, "
                 f"{ORDER_LABELS[self.source_order]} -> "
                 f"{ORDER_LABELS[self.target_order]}"]
        if self.source_order == self.target_order:
            lines.append("  byte order identical - no bytes changed")
        if self.resized_from is not None:
            lines.append(f"  resized from {self.resized_from} to {self.kind.size} bytes")
        if self.check is not None:
            lines.append(f"  {self.check.describe()}")
        return "\n".join(lines)


def convert_save(data: bytes, kind: SaveKind, source: str, target: str,
                 game: str | None = None) -> Conversion:
    """Rewrite a save from one tool's conventions into another's.

    Both ends are named, never inferred: the byte order belongs to the
    tool that wrote the file, and detection is wrong often enough that
    letting it drive this would corrupt saves (see detect_order).

    When the game is one we can validate, the result is checked before it
    is handed back, and a result that fails its own checksum is an error
    rather than a file the caller might write over a good save.
    """
    source_order = order_for_source(source, kind)
    target_order = order_for_source(target, kind)

    original_size = len(data)
    out = convert_order(data, source_order, target_order)
    out = normalize_size(out, kind)

    check = None
    if game is not None:
        checker = CHECKERS.get(game)
        if checker is not None:
            check = checker(out)
            if check.invalid:
                raise SaveError(
                    f"the converted save does not match {game}'s own "
                    f"{check.basis} ({check.describe()}). Refusing to hand "
                    f"back a file "
                    f"that the game would reject - check that the source "
                    f"and target really are what they were named as")

    return Conversion(
        data=out,
        kind=kind,
        source_order=source_order,
        target_order=target_order,
        resized_from=None if len(out) == original_size else original_size,
        check=check,
    )


# ---------------------------------------------------------------------------
# Working with files
# ---------------------------------------------------------------------------

def kind_for_file(path: str, data: bytes, requested: str | None = None) -> SaveKind:
    """Decide which chip a save file holds.

    Size narrows it, the extension usually settles the rest, and where it
    does not the caller has to say. 32 KiB is genuinely ambiguous - SRAM
    and a Controller Pak are the same size - and picking one silently
    would mean converting a save under the wrong assumptions.
    """
    if requested is not None:
        try:
            return SAVE_KINDS_BY_KEY[requested]
        except KeyError:
            known = ", ".join(SAVE_KINDS_BY_KEY)
            raise SaveError(f"unknown chip type {requested!r}. Known: {known}") from None

    by_size = kinds_for_size(len(data))
    if not by_size:
        sizes = ", ".join(f"{k.size} ({k.label})" for k in SAVE_KINDS)
        raise SaveError(
            f"{len(data)} bytes matches no N64 save chip. Expected one of: "
            f"{sizes}")
    if len(by_size) == 1:
        return by_size[0]

    by_ext = [k for k in kinds_for_extension(path) if k in by_size]
    if len(by_ext) == 1:
        return by_ext[0]

    # The flashcart calls every save ".sav" whatever the chip, so the
    # extension settles nothing for the commonest files on a real card.
    # The name still can: a save named after a game we know is that game's
    # chip type. Only accepted when exactly one candidate matches.
    named = [k for k in by_size if identify_game(path, k, data) is not None]
    if len(named) == 1:
        return named[0]

    options = ", ".join(f"{k.key} ({k.label})" for k in by_size)
    raise SaveError(
        f"{len(data)} bytes fits more than one chip and neither the name nor "
        f"the extension settles it. Say which with --save-type: {options}")


def describe_file(path: str, data: bytes, requested: str | None = None) -> str:
    """A report on a save file: what it is, what is in it, what we can say
    about its byte order - clearly separating the measured from the
    guessed."""
    lines = [f"{os.path.basename(path)}  ({len(data)} bytes)"]
    try:
        kind = kind_for_file(path, data, requested)
    except SaveError as exc:
        lines.append(f"  chip type : UNCLEAR - {exc}")
        return "\n".join(lines)

    lines.append(f"  chip type : {kind.label}")

    if not data or len(set(data)) <= 1:
        lines.append("  contents  : empty - nothing written to this chip yet")
        return "\n".join(lines)

    game = identify_game(path, kind, data)
    if game:
        check = CHECKERS[game](data)
        lines.append(f"  game      : {game}")
        lines.append(f"  as stored : {check.describe()}")
        if not check.ok:
            for order in ORDERS:
                if order == ORDER_RAW:
                    continue
                if CHECKERS[game](reorder(data, order)).ok:
                    lines.append(f"  but valid as {ORDER_LABELS[order]} - the file "
                                 f"is in that arrangement")
                    break
    else:
        lines.append("  game      : not recognised - no validation available")

    guess = detect_order(data)
    verdict = ORDER_LABELS[guess.order] if guess.order else "undecided"
    lines.append(f"  hint      : looks like {verdict} (unreliable - one save in "
                 f"five is called wrongly; go by the source instead)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------

def region_note(_game_key: str | None = None) -> str:
    """What can be said about moving this save between PAL and NTSC.

    Placeholder for the per-game profile database. Until a game has an
    entry there is only one truthful answer, and it is the right one for
    most titles: the save layout does not depend on the region, so the
    file is used as it is - after any byte order fix its own checksum
    still has to validate in the game.

    Evidence so far, from a 132-save card: of nine titles present in more
    than one region, all nine use the same chip size in both. For Super
    Mario 64 the layout was checked rather than assumed - the same block
    structure, the same markers and the same checksum rule validate the
    European save and every American one, 10 blocks of 10.
    """
    return ("No region-specific difference is recorded for this game. The "
            "save layout is written by the game's own code and is normally "
            "identical across PAL and NTSC, so the file should be used "
            "unchanged apart from the byte order.")
