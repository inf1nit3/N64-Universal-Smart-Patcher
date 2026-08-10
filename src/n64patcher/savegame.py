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
    order: str
    #: How the order was established. Empty means unverified, and such an
    #: entry must not ship.
    evidence: str
    #: Chip types the evidence actually covers. A measurement on one chip
    #: does not carry to the others: the byte order differences reported
    #: in the wild are mostly about SRAM and FlashRAM, so an EEPROM file
    #: proving one tool's convention proves it for EEPROM and no more.
    verified_kinds: tuple[str, ...]


SOURCES: tuple[SaveSource, ...] = (
    SaveSource(
        "sc64", "SummerCart64 / N64FlashcartMenu", ORDER_RAW,
        "Measured on a 132-save card: the Ocarina of Time save carries its "
        "'ZELDAZ' marker unscrambled at 0x3c, with the backup copy at "
        "0x3d2c, so the file is in chip order. Saves of all five chip types "
        "were present and none showed a differing arrangement",
        ALL_KINDS),
    SaveSource(
        "mupen64plus", "mupen64plus", ORDER_RAW,
        "Measured on its Super Mario 64 EEPROM save: the game's own "
        "checksum passes on all 10 written blocks as stored, and fails on "
        "all 10 under either swap. EEPROM only - no SRAM or FlashRAM file "
        "from this emulator has been examined",
        (EEPROM_4K, EEPROM_16K)),
    SaveSource(
        "hardware", "Real cartridge / chip dump", ORDER_RAW,
        "Chip order by definition - this is what the save chip holds",
        ALL_KINDS),
)

SOURCES_BY_KEY = {s.key: s for s in SOURCES}


def order_for_source(key: str, kind: SaveKind | None = None) -> str:
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

    if kind is not None and kind.key not in source.verified_kinds:
        covered = ", ".join(source.verified_kinds)
        raise SaveError(
            f"{source.label} has only been verified for: {covered}. Its "
            f"byte order for {kind.label} is unmeasured, and guessing it "
            f"would risk scrambling the save")
    return source.order


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
    """Result of validating a save against the game's own checksum."""
    valid: int
    invalid: int
    unused: int

    @property
    def ok(self) -> bool:
        return self.invalid == 0 and self.valid > 0

    def describe(self) -> str:
        if self.valid == 0 and self.invalid == 0:
            return "empty save - nothing has been written to this chip yet"
        if self.ok:
            return (f"all {self.valid} written blocks pass the game's own "
                    f"checksum")
        return (f"{self.invalid} of {self.valid + self.invalid} written "
                f"blocks fail the game's own checksum")


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


#: Games whose save can be validated. Keyed by the name used in reports.
CHECKERS = {"Super Mario 64": check_sm64}


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
