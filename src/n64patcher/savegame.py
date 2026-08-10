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


@dataclass(frozen=True)
class SaveKind:
    key: str
    label: str
    size: int
    extensions: tuple[str, ...]
    #: Byte an erased chip of this type reads as. Used when padding a short
    #: file up to the hardware size, so the padding looks like untouched
    #: chip rather than written zeroes.
    erased: int


SAVE_KINDS: tuple[SaveKind, ...] = (
    SaveKind(EEPROM_4K, "EEPROM 4 Kbit", 512, (".eep",), 0x00),
    SaveKind(EEPROM_16K, "EEPROM 16 Kbit", 2 * 1024, (".eep",), 0x00),
    SaveKind(SRAM_256K, "SRAM 256 Kbit", 32 * 1024, (".sra", ".srm"), 0x00),
    SaveKind(SRAM_768K, "SRAM 768 Kbit", 96 * 1024, (".sra", ".srm"), 0x00),
    SaveKind(FLASHRAM_1M, "FlashRAM 1 Mbit", 128 * 1024, (".fla", ".flash"), 0xFF),
    SaveKind(CONTROLLER_PAK, "Controller Pak", 32 * 1024, (".mpk", ".pak"), 0x00),
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
    """Guess which byte order *data* is stored in. A hint, not a verdict."""
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
# Size normalisation
# ---------------------------------------------------------------------------

class SaveError(ValueError):
    """A save file cannot be handled as asked."""


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
# Region
# ---------------------------------------------------------------------------

def region_note(_game_key: str | None = None) -> str:
    """What can be said about moving this save between PAL and NTSC.

    Placeholder for the per-game profile database. Until a game has an
    entry there is only one truthful answer, and it is the right one for
    most titles: the save layout does not depend on the region, so the
    file is used as it is - after any byte order fix its own checksum
    still has to validate in the game.
    """
    return ("No region-specific difference is recorded for this game. The "
            "save layout is written by the game's own code and is normally "
            "identical across PAL and NTSC, so the file should be used "
            "unchanged apart from the byte order.")
