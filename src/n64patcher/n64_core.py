"""Core engine for the Universal N64 ROM Inspector & Smart Patcher.

Pure-standard-library module: no PyQt6 dependency so it can be reused by
the GUI, the CLI, and the unit tests.

Patch pipeline overview (see patch_rom):
  1. Convert input to native big-endian .z64 (rejects unknown formats)
  2. If hi-res requested and a verified SubDrag .xdelta exists for the
     title, apply it to the CLEAN source first (xdelta patches are built
     against pristine dumps - applying them after other modifications
     fails)
  3. Apply VI filter options (No-AA via u64aap when enabled, dynamic
     instruction-mask fallback, dither/divot/gamma flags)
  4. Hi-res fallback: Smart VI Mode Table engine (width 320 -> 640 on
     structurally verified OSViMode entries only)
  5. Recalculate boot checksums (bundled rn64crc.exe when available,
     otherwise the built-in pure-Python CRC engine) and write a new
     output file; originals are never modified
"""

import csv
import hashlib
import json
import mmap
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass

from . import patchdb
from ._version import __version__

# Public alias: the GUI title bar and `--version` read core.VERSION.
# An assignment rather than `import ... as VERSION`, which linters strip
# as an unused import.
VERSION = __version__

# Populated at import by load_patch_db; surfaced via patch_db_problems().
_patch_db_problems: list[str] = []

# ---------------------------------------------------------------------------
# Paths (frozen-aware for PyInstaller bundles)
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    EXE_DIR = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = BUNDLE_DIR


def get_asset_path(*relative_parts):
    p1 = os.path.join(BUNDLE_DIR, *relative_parts)
    if os.path.exists(p1):
        return p1
    return os.path.join(EXE_DIR, *relative_parts)


def _is_runnable(path):
    """True if *path* is a file we can actually execute. On non-Windows
    platforms the bundled .exe helpers are not runnable, so missing the
    executable bit must count as 'tool unavailable'."""
    if not os.path.isfile(path):
        return False
    if sys.platform == "win32":
        return True
    return os.access(path, os.X_OK)


def _resolve_tool(bundled_path, *system_names):
    """Return the bundled helper if runnable, otherwise fall back to a
    same-named tool on PATH (e.g. a system xdelta3 on macOS/Linux)."""
    if _is_runnable(bundled_path):
        return bundled_path
    for name in system_names:
        found = shutil.which(name)
        if found:
            return found
    return bundled_path


U64AAP_PATH = _resolve_tool(
    get_asset_path("N64noAAPatcher", "additionals", "u64aap.exe"), "u64aap")
RN64CRC_PATH = _resolve_tool(
    get_asset_path("N64noAAPatcher", "additionals", "rn64crc.exe"), "rn64crc")
XDELTA3_PATH = _resolve_tool(
    get_asset_path("N64noAAPatcher", "additionals", "xdelta3.exe"),
    "xdelta3", "xdelta")
HIRES_PATCHES_DIR = get_asset_path("N64noAAPatcher", "hires_patches")

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SUBPROCESS_TIMEOUT = 120  # seconds; u64aap/xdelta/rn64crc are all fast

ROM_EXTENSIONS = (".z64", ".n64", ".v64")
OUTPUT_TAGS = (" [HR+NoAA]", " [640p]", " [NoAA]", " [NoDither]", " [PATCHED]",
               " [COMMUNITY]", " [CRCFIX]")
TEMP_SUFFIXES = (".temp.z64", ".patched.z64", ".xdelta_out.z64",
                 ".stripped.z64")


def get_log_path():
    """Per-user log location (EXE_DIR is read-only for installed bundles)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        log_dir = os.path.join(base, "N64SmartPatcher")
    else:
        log_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "n64-smart-patcher")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "N64_Patcher_Log.txt")


# Batch workers append from several threads; without this they interleave
# mid-line in the log file.
_LOG_LOCK = threading.Lock()


def append_log(lines):
    path = get_log_path()
    with _LOG_LOCK, open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def check_tools():
    """Availability of the external helpers. All stages degrade gracefully
    when a tool is missing; CRC fixing always works via the built-in
    pure-Python engine ('crc_native')."""
    return {
        "u64aap": _is_runnable(U64AAP_PATH),
        "rn64crc": _is_runnable(RN64CRC_PATH),
        "xdelta3": _is_runnable(XDELTA3_PATH),
        "hires_patches": os.path.isdir(HIRES_PATCHES_DIR),
        "crc_native": True,
    }


def is_rom_file(path):
    return os.path.splitext(path)[1].lower() in ROM_EXTENSIONS


def is_tool_output(path):
    """True for files this tool generated (tagged outputs, temp files), so
    folder drops don't re-ingest previous results."""
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    if ext.lower() in ROM_EXTENSIONS and any(stem.endswith(t) for t in OUTPUT_TAGS):
        return True
    return any(name.endswith(s) for s in TEMP_SUFFIXES)


# ---------------------------------------------------------------------------
# ROM format / endianness handling
# ---------------------------------------------------------------------------

FORMATS = {
    "80371240": ("z64", ".z64 (Big-Endian Native)"),
    "37804012": ("v64", ".v64 (Byte-Swapped BADC)"),
    "40123780": ("n64", ".n64 (Little-Endian DCBA)"),
}


def detect_format(data):
    """Return (format_key, label) or (None, label) for unknown/short data."""
    if len(data) < 4:
        return None, "Too small to be an N64 ROM"
    magic = data[:4].hex().upper()
    if magic in FORMATS:
        return FORMATS[magic]
    return None, f"Unknown (Magic 0x{magic})"


def to_big_endian(data, fmt):
    """Convert v64/n64 byte order to native big-endian z64.

    Odd trailing bytes (overdumps) are passed through untouched instead of
    crashing struct.unpack."""
    if fmt == "v64":
        n = len(data) - (len(data) % 2)
        ba = bytearray(data[:n])
        ba[0::2], ba[1::2] = bytes(ba[1::2]), bytes(ba[0::2])
        return bytes(ba) + data[n:]
    if fmt == "n64":
        n = len(data) - (len(data) % 4)
        ba = bytearray(data[:n])
        ba[0::4], ba[1::4], ba[2::4], ba[3::4] = (
            bytes(ba[3::4]), bytes(ba[2::4]), bytes(ba[1::4]), bytes(ba[0::4]))
        return bytes(ba) + data[n:]
    return data


def from_big_endian(data, fmt):
    """Inverse of to_big_endian. Both orderings are byte permutations that
    are their own inverse, so this delegates - it exists to make the
    round-trip direction obvious at the call site."""
    return to_big_endian(data, fmt)


def ensure_z64(input_path, out_path):
    """Write a native big-endian copy of input_path to out_path.
    Returns False for files that are not recognizable N64 ROMs."""
    with open(input_path, "rb") as f:
        data = f.read()
    fmt, _label = detect_format(data)
    if fmt is None:
        return False
    with open(out_path, "wb") as f:
        f.write(to_big_endian(data, fmt))
    return True


# ---------------------------------------------------------------------------
# Pure-Python N64 boot CRC engine (CRC1/CRC2 at header 0x10/0x14)
#
# Port of the well-known algorithm (as used by Project64/rn64crc-class
# tools): the CIC boot-chip is identified by summing the big-endian words
# of the bootcode region, then a seed derived from the CIC drives a
# register-mixing pass over the first 1 MiB. Works on every platform -
# no rn64crc.exe needed.
# ---------------------------------------------------------------------------

_MASK32 = 0xFFFFFFFF
CRC_DATA_OFFSET = 0x1000
CRC_DATA_LENGTH_DEFAULT = 0x100000

# CIC chips -> 64-bit sum of the big-endian words at 0x40..0x1000
CIC_SUMS = {
    0x000000D0027FDF31: "6101",
    0x000000CFFB631223: "6101",
    0x000000C34B2826B8: "6101",  # iQue
    0x0000002F35CF0DE9: "6101",  # iQue (Paper Mario)
    0x000000C92ADFE50A: "6101",  # iQue (Sin and Punishment)
    0x000000D057C85244: "6102",
    0x0000007C56242373: "6102",  # libdragon IPL3
    0x000000D6497E414B: "6103",
    0x0000011A49F60E96: "6105",
    0x000000D6D5BE5580: "6106",
    0x000001053BC19870: "5167",  # 64DD conversion CIC
    0x000000D2E53EF008: "8303",  # 64DD IPL
    0x000000D2E53EF39F: "8401",  # 64DD IPL tool
    0x000000D2E53E5DDA: "8501",  # 64DD IPL US
}
CIC_SUM_ALECK64 = 0x000000A5F80BF620  # partial sum 0x40..0xC00

# CIC chip -> (seed, CRC data length)
CIC_SEEDS = {
    "6101": (0xF8CA4DDC, CRC_DATA_LENGTH_DEFAULT),
    "6102": (0xF8CA4DDC, CRC_DATA_LENGTH_DEFAULT),
    "6103": (0xA3886759, CRC_DATA_LENGTH_DEFAULT),
    "6105": (0xDF26F436, CRC_DATA_LENGTH_DEFAULT),
    "6106": (0x1FEA617A, CRC_DATA_LENGTH_DEFAULT),
    "8501": (0x861AE3A7, 0x000A0000),
    "8303": (0x8331D4CA, 0x000A0000),
    "8401": (0x0D8303E2, 0x000A0000),
    "5101": (0x95104FDD, CRC_DATA_LENGTH_DEFAULT),
}


def _be_word(data, offset):
    """Big-endian 32-bit word at offset; missing bytes read as zero
    (short/homebrew images are padded, mirroring empty flash)."""
    if offset + 4 <= len(data):
        return struct.unpack_from(">I", data, offset)[0]
    word = 0
    for i in range(4):
        p = offset + i
        word = (word << 8) | (data[p] if p < len(data) else 0)
    return word


def _be_words(data, offset, count):
    """`count` big-endian words starting at `offset`, zero-padded when the
    image is short. One struct.unpack beats `count` slice-and-shift calls
    by a wide margin, and this runs 262k times per ROM."""
    end = offset + count * 4
    chunk = bytes(data[offset:end])
    if len(chunk) < count * 4:
        chunk += b"\x00" * (count * 4 - len(chunk))
    return struct.unpack(f">{count}I", chunk)


def detect_cic_chip(data):
    """Identify the CIC boot chip from the bootcode region (0x40..0x1000).
    Returns a chip string ('6102', ...) or None if unknown."""
    if len(data) < 0x44:
        return None
    words = _be_words(data, 0x40, (0x1000 - 0x40) // 4)
    aleck_words = (0xC00 - 0x40) // 4
    aleck_sum = sum(words[:aleck_words])  # Aleck64 only covers 0x40..0xC00
    total = aleck_sum + sum(words[aleck_words:])
    chip = CIC_SUMS.get(total)
    if chip is not None:
        return chip
    if aleck_sum == CIC_SUM_ALECK64:
        return "5101"
    return None


def calculate_n64_crc(data, chip=None):
    """Compute (crc1, crc2) for a big-endian z64 image. Returns None if
    the CIC chip is unknown. Algorithm mirrors the hardware-derived
    implementations used by emulators and flashcart tooling."""
    if chip is None:
        chip = detect_cic_chip(data)
    if chip is None or chip not in CIC_SEEDS:
        return None

    seed, length = CIC_SEEDS[chip]
    if chip == "5101" and _be_word(data, 0x8) == 0x80100400:
        length = 0x003FE000

    a3 = t2 = t3 = s0 = a2 = t4 = seed

    words = _be_words(data, CRC_DATA_OFFSET, length // 4)
    # CIC-6105 mixes in a rotating window of the bootcode at 0x750..0x850.
    table = _be_words(data, 0x750, 0x40) if chip == "6105" else None
    is_8303 = chip == "8303"
    is_carry_xor = chip in ("8501", "8303")
    # Local rebind: the loop below runs 262k times and touches this on
    # nearly every line, so a global lookup each time is not free.
    mask = _MASK32

    for i, d in enumerate(words):
        carry_sum = a3 + d
        a1 = carry_sum & mask
        if carry_sum > mask:
            if is_carry_xor:
                t2 ^= t3
            else:
                t2 = (t2 + 1) & mask

        shift = d & 0x1F
        rot = ((d << shift) | (d >> (32 - shift))) & mask if shift else d

        a3 = a1
        t3 ^= d
        s0 = (s0 + rot) & mask
        # Reference: `if (t2 > d) t2 ^= r; else t2 ^= t6 ^ d;` - the
        # comparison is strictly greater, so a2 == d takes the a3 ^ d path.
        if a2 > d:
            if is_8303:
                a2 = (a2 + rot) & mask
            else:
                a2 ^= rot
        else:
            a2 ^= a3 ^ d

        if table is not None:  # noqa: SIM108 - a ternary here buries the comment
            # The original walked bytes (0x750 + (byte_index & 0xFF)); as a
            # word index into the 64-word table that is i & 0x3F.
            t4 = (t4 + (d ^ table[i & 0x3F])) & mask
        else:
            t4 = (t4 + (d ^ s0)) & mask

    if chip == "6103":
        crc1 = ((a3 ^ t2) + t3) & _MASK32
        crc2 = ((s0 ^ a2) + t4) & _MASK32
    elif chip == "6106":
        crc1 = ((a3 * t2) + t3) & _MASK32
        crc2 = ((s0 * a2) + t4) & _MASK32
    elif chip == "5101":
        crc1 = ((a3 ^ t2) + t3) & _MASK32
        crc2 = ((s0 ^ a2) + t4) & _MASK32
    else:
        crc1 = a3 ^ t2 ^ t3
        crc2 = s0 ^ a2 ^ t4
    return crc1, crc2


def crc_header_is_valid(path):
    """True when the CRC1/CRC2 stored at 0x10/0x14 match a fresh
    computation. Used to check an external CRC tool actually did its job,
    and by verify_output."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    fmt, _label = detect_format(data)
    if fmt is None:
        return False
    be = to_big_endian(data, fmt)
    crc = calculate_n64_crc(be)
    if crc is None:
        return False
    stored = (int.from_bytes(be[0x10:0x14], "big"),
              int.from_bytes(be[0x14:0x18], "big"))
    return stored == crc


def fix_rom_crc_native(path):
    """Recalculate CRC1/CRC2 in pure Python and stamp them into the file
    at 0x10/0x14. Byte-swapped .v64/.n64 images are handled by computing
    on a big-endian view and writing the result back in the file's own
    byte order, so fixing a checksum never silently changes the format.
    Returns (True, message) or (False, message)."""
    with open(path, "rb") as f:
        raw = f.read()
    fmt, label = detect_format(raw)
    if fmt is None:
        return False, f"Not a recognizable N64 ROM ({label})"
    data = bytearray(to_big_endian(raw, fmt))
    chip = detect_cic_chip(data)
    if chip is None:
        return False, "Unknown CIC boot chip - cannot compute CRC"
    crc = calculate_n64_crc(data, chip)
    if crc is None:
        return False, f"Unsupported CIC chip '{chip}'"
    crc1, crc2 = crc
    data[0x10:0x14] = struct.pack(">I", crc1)
    data[0x14:0x18] = struct.pack(">I", crc2)
    with open(path, "wb") as f:
        f.write(from_big_endian(bytes(data), fmt))
    order = "" if fmt == "z64" else f", .{fmt} byte order preserved"
    return True, f"CRC1/CRC2 recalculated natively (CIC-{chip}{order})"


# ---------------------------------------------------------------------------
# Smart VI Mode Table Engine
# Searches for N64 SDK OSViMode data structures: the 32-bit width word
# (320 = 0x00000140) paired with a hardware NTSC/PAL/M-PAL burst timing
# constant. Structurally unique -> zero false positives.
# ---------------------------------------------------------------------------

NTSC_BURST = bytes.fromhex("03E52239")
PAL_BURST = bytes.fromhex("0404233A")
MPAL_BURST = bytes.fromhex("04651E39")
ALL_BURSTS = (NTSC_BURST, PAL_BURST, MPAL_BURST)

WIDTH_320_DATA = bytes.fromhex("00000140")
WIDTH_640_DATA = bytes.fromhex("00000280")


def find_vi_tables(data, width=WIDTH_320_DATA):
    """Find all VI mode table entries by width + burst signature."""
    tables = []
    pos = 0
    while True:
        pos = data.find(width, pos)
        if pos == -1:
            break
        next_4 = data[pos + 4:pos + 8]
        if next_4 in ALL_BURSTS:
            tv = "NTSC" if next_4 == NTSC_BURST else ("PAL" if next_4 == PAL_BURST else "M-PAL")
            tables.append({"offset": pos, "tv": tv})
        pos += 4
    return tables


def scan_vi_tables_file(rom_path, width=WIDTH_320_DATA):
    """Memory-mapped VI table scan for big-endian .z64 files (avoids
    loading the whole ROM for inspection). Falls back to an in-memory
    scan when mmap is unavailable. Same result shape as find_vi_tables."""
    if not os.path.isfile(rom_path) or os.path.getsize(rom_path) < 8:
        return []
    try:
        with open(rom_path, "rb") as f, \
                mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            tables = []
            size = len(mm)
            pos = 0
            while True:
                pos = mm.find(width, pos)
                if pos == -1 or pos + 8 > size:
                    break
                next_4 = mm[pos + 4:pos + 8]
                if next_4 in ALL_BURSTS:
                    tv = ("NTSC" if next_4 == NTSC_BURST
                          else ("PAL" if next_4 == PAL_BURST else "M-PAL"))
                    tables.append({"offset": pos, "tv": tv})
                pos += 4
            return tables
    except (ValueError, OSError):
        with open(rom_path, "rb") as f:
            return find_vi_tables(f.read(), width)


def apply_smart_hires_patch(z64_path):
    """Patch verified VI mode tables from 320 to 640 pixel width.
    Only modifies width fields followed by a known burst constant."""
    with open(z64_path, "rb") as f:
        data = bytearray(f.read())

    tables = find_vi_tables(data)
    if not tables:
        return False, 0, "No VI mode tables found"

    for t in tables:
        data[t["offset"]:t["offset"] + 4] = WIDTH_640_DATA

    with open(z64_path, "wb") as f:
        f.write(data)
    return True, len(tables), f"Patched {len(tables)} VI table(s) to 640x480"


# ---------------------------------------------------------------------------
# Dynamic VI instruction-mask patcher (fallback when u64aap has no DB entry)
#
# This is a pattern rewrite, so the search window is what keeps it honest.
# Three constraints bound it:
#   * region  - only the game's code segment is considered. Below 0x1000
#               sits the IPL3 bootcode, which must never be touched (it is
#               exactly what detect_cic_chip sums to identify the CIC);
#               past CODE_REGION_END sits compressed asset data, where a
#               4-byte match is coincidence rather than an instruction.
#   * align   - MIPS instructions are word-aligned, so a hit at an offset
#               that is not a multiple of 4 cannot be the instruction.
#   * density - real libultra code has a handful of these sites. A flood
#               means the scan is walking data that merely looks like code;
#               patching then does far more damage than leaving AA on.
# ---------------------------------------------------------------------------

CODE_REGION_START = 0x1000
CODE_REGION_END = 8 * 1024 * 1024
_INSTR_ALIGN = 4
MAX_DYNAMIC_PATCH_SITES = 64

DITHER_PATTERN = bytes.fromhex("31cf0040")             # andi $t7, $t6, 0x40
DITHER_REPLACEMENT = bytes.fromhex("31cf0000")         # andi $t7, $t6, 0x00
DITHER_BRANCH = bytes.fromhex("11e0000d")              # beq  $t7, $zero, +0xd
DITHER_BRANCH_REPLACEMENT = bytes.fromhex("1000000d")  # b    +0xd
AA_PATTERN = bytes.fromhex("30423000")                 # andi $v0, $v0, 0x3000
AA_REPLACEMENT = bytes.fromhex("30422000")             # andi $v0, $v0, 0x2000


def find_instruction_sites(data, pattern, start=CODE_REGION_START,
                           end=CODE_REGION_END):
    """Word-aligned occurrences of *pattern* within [start, end)."""
    sites = []
    pos = max(start, 0)
    limit = min(end, len(data))
    while pos < limit:
        pos = data.find(pattern, pos, limit)
        if pos == -1:
            break
        if pos % _INSTR_ALIGN == 0:
            sites.append(pos)
            pos += _INSTR_ALIGN
        else:
            pos += 1
    return sites


def apply_dynamic_vi_patch(z64_path, no_aa=True, no_dither=True, log=None):
    """Apply instruction-mask patches inside the code segment only. Each
    option is honored independently. Returns the set of applied labels
    ('NoAA', 'NoDither'); an implausibly dense match set is reported via
    *log* and left alone rather than rewritten."""
    with open(z64_path, "rb") as f:
        data = bytearray(f.read())

    applied = set()
    if len(data) <= CODE_REGION_START:
        return applied

    def note(msg):
        if log:
            log(msg)

    def sites_for(pattern, label):
        found = find_instruction_sites(data, pattern)
        if len(found) > MAX_DYNAMIC_PATCH_SITES:
            note(f"  Dynamic VI: {len(found)} {label} candidates is "
                 f"implausible for code - skipping to avoid corrupting data")
            return []
        return found

    if no_dither:
        for pos in sites_for(DITHER_PATTERN, "dither"):
            data[pos:pos + 4] = DITHER_REPLACEMENT
            if data[pos + 4:pos + 8] == DITHER_BRANCH:
                data[pos + 4:pos + 8] = DITHER_BRANCH_REPLACEMENT
            applied.add("NoDither")

    if no_aa:
        for pos in sites_for(AA_PATTERN, "AA"):
            data[pos:pos + 4] = AA_REPLACEMENT
            applied.add("NoAA")

    if applied:
        with open(z64_path, "wb") as f:
            f.write(data)
    return applied


# ---------------------------------------------------------------------------
# Verified per-dump patch recipes, keyed on the exact dump they target
#
# An xdelta delta applies only to the ROM it was built against, so the
# header CRC1/CRC2 pair is the right key. Every value below was derived by
# actually applying the delta to candidate dumps and recording which one
# succeeded - not transcribed from a database.
#
# This replaces matching on the ROM's internal title, which was wrong twice:
# the keys "BANJO KAZOOIE" and "FORSAKEN 64" never matched the real titles
# "Banjo-Kazooie" (hyphen) and "Forsaken" (no "64"), so those two games
# silently never received their patch. Title matching also could not
# distinguish revisions - the Banjo delta applies to Rev A only, and would
# have been attempted and failed on the far more common base USA dump.
#
# The recipes themselves live in patches/*.json rather than in this file, so
# the supported set can grow without a code change. See patchdb.
# ---------------------------------------------------------------------------

# The recipes now live in data files (see patchdb). Loaded once at import:
# a per-ROM reload would re-read the directory for every file in a batch.
PATCH_DB = patchdb.load_patch_db(on_error=_patch_db_problems.append)

# Compatibility view: {(crc1, crc2): (xdelta filename, human label)} for the
# hi-res entries. Kept because it is the shape the pipeline and its tests
# already speak; find_patch_entry() exposes the full recipe.
SUBDRAG_PATCHES = {
    key: (entry["operations"][0].get("file", ""), entry["name"])
    for key, entry in PATCH_DB.items()
    if "hires" in entry["provides"]
    and entry["operations"] and entry["operations"][0]["type"] == "xdelta"
}


def patch_db_problems():
    """Messages from recipe files that failed to load. Empty when clean."""
    return list(_patch_db_problems)


def find_patch_entry(crc1, crc2):
    """Full recipe for this exact dump, or None. Accepts ints or hex text."""
    try:
        key = (int(crc1, 16) if isinstance(crc1, str) else int(crc1),
               int(crc2, 16) if isinstance(crc2, str) else int(crc2))
    except (TypeError, ValueError):
        return None
    return PATCH_DB.get(key)


def get_subdrag_patch(crc1, crc2):
    """Path of the verified SubDrag patch for this exact dump, or None.

    *crc1*/*crc2* are the header checksums, as ints or hex strings.
    """
    if not os.path.isdir(HIRES_PATCHES_DIR):
        return None
    try:
        key = (int(crc1, 16) if isinstance(crc1, str) else int(crc1),
               int(crc2, 16) if isinstance(crc2, str) else int(crc2))
    except (TypeError, ValueError):
        return None
    entry = SUBDRAG_PATCHES.get(key)
    if entry is None:
        return None
    candidate = os.path.join(HIRES_PATCHES_DIR, entry[0])
    if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
        return candidate
    return None


# ---------------------------------------------------------------------------
# Hi-res capability
#
# Widening an OSViMode entry from 320 to 640 changes ONE field. The struct
# also carries xScale/yScale, and the game separately allocated a 320-wide
# framebuffer and draws into it with RDP coordinates that assume that width.
# Flip the width alone and the VI reads two lines' worth of data per line
# while the game keeps drawing at the old scale: doubled image, UI in the
# wrong place, menus rendered at the wrong size.
#
# Confirmed on hardware (SummerCart64, 2026-08): every ROM patched by the
# generic table flip rendered incorrectly; the same ROMs were fine
# unpatched. Real hi-res needs the framebuffer allocation and the RDP
# pipeline patched too, which is exactly what the hand-made SubDrag deltas
# do - and why they exist for only a handful of dumps.
#
# So hi-res is offered only where it is known to work:
#   verified - an exact-CRC SubDrag delta exists for this dump
#   native   - the ROM already ships 640-wide VI tables; nothing to do
#   unsupported - only the generic width flip applies; known broken
# ---------------------------------------------------------------------------

HIRES_VERIFIED = "verified"
HIRES_NATIVE = "native"
HIRES_UNSUPPORTED = "unsupported"


def hires_support(info):
    """Classify a ROM's 640x480 support. Returns (status, reason)."""
    if get_subdrag_patch(info.get("crc1"), info.get("crc2")):
        return HIRES_VERIFIED, "Verified SubDrag patch exists for this exact dump"
    if info.get("is_hires_640x480"):
        return HIRES_NATIVE, "ROM already renders at 640x480; no patch needed"
    if info.get("vi_table_count"):
        return (HIRES_UNSUPPORTED,
                "No verified patch for this dump. Widening the VI tables alone "
                "leaves the framebuffer and RDP scaling at 320, which renders "
                "incorrectly on hardware")
    return HIRES_UNSUPPORTED, "No patchable VI mode tables found"


def patch_includes_noaa(patch_path):
    return "noaa" in os.path.basename(patch_path).lower().replace(" ", "").replace("-", "")


def try_subdrag_xdelta(patch_file, source_z64, output_z64):
    """Apply a SubDrag .xdelta patch. The source MUST be the pristine ROM -
    xdelta deltas are built against clean dumps and fail on modified data."""
    if not _is_runnable(XDELTA3_PATH):
        return False, "xdelta3 not found (bundled exe not runnable here; install xdelta3 for SubDrag support)"
    cmd = [XDELTA3_PATH, "-d", "-s", source_z64, patch_file, output_z64]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                             creationflags=CREATE_NO_WINDOW, timeout=SUBPROCESS_TIMEOUT)
        if res.returncode == 0 and os.path.isfile(output_z64) and os.path.getsize(output_z64) > 0:
            return True, f"SubDrag verified patch applied ({os.path.basename(patch_file)})"
        return False, f"xdelta3 failed (ROM version mismatch?): {res.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "xdelta3 timed out"
    except OSError as e:
        return False, f"xdelta3 error: {e}"


# ---------------------------------------------------------------------------
# ROM inspection
# ---------------------------------------------------------------------------

REGION_MAP = {
    "7": "Beta / Prototype",
    "A": "Asia (NTSC 60Hz)",
    "B": "Brazil (M-PAL 60Hz)",
    "C": "China (iQue NTSC 60Hz)",
    "D": "Germany (PAL 50Hz)",
    "E": "USA / North America (NTSC 60Hz)",
    "F": "France (PAL 50Hz)",
    "G": "Gateway 64 (NTSC 60Hz)",
    "H": "Netherlands (PAL 50Hz)",
    "I": "Italy (PAL 50Hz)",
    "J": "Japan (NTSC-J 60Hz)",
    "K": "South Korea (NTSC 60Hz)",
    "L": "Gateway 64 (PAL 50Hz)",
    "P": "Europe / PAL (50Hz)",
    "S": "Spain (PAL 50Hz)",
    "U": "Australia (PAL 50Hz)",
    "W": "Scandinavia (PAL 50Hz)",
    "X": "Europe (PAL 50Hz, alt)",
    "Y": "Europe (PAL 50Hz, alt)",
}

# Detection reads exactly the window apply_dynamic_vi_patch writes to, so
# "already patched" can never disagree with what the patcher would do.
AA_SCAN_LIMIT = CODE_REGION_END


def _hash_file(path, chunk_size=1024 * 1024):
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
    return md5.hexdigest().upper(), sha1.hexdigest().upper()


def inspect_rom_details(rom_path, with_hashes=False):
    info = {
        "filename": os.path.basename(rom_path),
        "path": rom_path,
        "size_mb": round(os.path.getsize(rom_path) / (1024 * 1024), 2),
        "format": "Unknown",
        "title": "Unknown",
        "game_id": "Unknown",
        "region": "Unknown",
        "country_code": "?",
        "crc1": "Unknown",
        "crc2": "Unknown",
        "no_aa": False,
        "no_dither": False,
        "is_60fps_or_mod": False,
        "is_hires_640x480": False,
        "is_mixed_resolution": False,
        "vi_table_count": 0,
        "vi_table_640_count": 0,
        "has_subdrag_patch": False,
        "hires_support": HIRES_UNSUPPORTED,
        "hires_support_reason": "ROM not recognized",
    }

    fn_lower = os.path.basename(rom_path).lower()
    if "60_fps" in fn_lower or "60fps" in fn_lower or "redux" in fn_lower:
        info["is_60fps_or_mod"] = True

    with open(rom_path, "rb") as f:
        head = f.read(64)

    fmt, label = detect_format(head)
    info["format"] = label
    if fmt is None or len(head) < 64:
        return info

    if fmt == "z64":
        # Fast path: header fields from the first 64 bytes, AA/dither
        # heuristics from the code region, VI tables via mmap scan -
        # no full in-memory copy of the ROM is needed.
        info["title"] = head[32:52].decode("ascii", errors="ignore").strip()
        info["game_id"] = head[59:63].decode("ascii", errors="ignore").strip()
        info["crc1"] = head[16:20].hex().upper()
        info["crc2"] = head[20:24].hex().upper()

        country_byte = head[62]
        country_code = chr(country_byte) if 32 <= country_byte < 127 else "?"
        info["country_code"] = country_code
        info["region"] = REGION_MAP.get(country_code, f"Unknown ({country_code})")

        with open(rom_path, "rb") as f:
            scan_region = f.read(AA_SCAN_LIMIT)
        info["no_dither"] = bool(find_instruction_sites(scan_region,
                                                        DITHER_REPLACEMENT))
        info["no_aa"] = bool(find_instruction_sites(scan_region, AA_REPLACEMENT))

        vi_tables_320 = scan_vi_tables_file(rom_path, WIDTH_320_DATA)
        vi_tables_640 = scan_vi_tables_file(rom_path, WIDTH_640_DATA)
    else:
        # Byte-swapped formats need a full conversion pass first.
        with open(rom_path, "rb") as f:
            full_bytes = f.read()
        full_be = to_big_endian(full_bytes, fmt)

        info["title"] = full_be[32:52].decode("ascii", errors="ignore").strip()
        info["game_id"] = full_be[59:63].decode("ascii", errors="ignore").strip()
        info["crc1"] = full_be[16:20].hex().upper()
        info["crc2"] = full_be[20:24].hex().upper()

        country_byte = full_be[62]
        country_code = chr(country_byte) if 32 <= country_byte < 127 else "?"
        info["country_code"] = country_code
        info["region"] = REGION_MAP.get(country_code, f"Unknown ({country_code})")

        scan_region = full_be[:AA_SCAN_LIMIT]
        info["no_dither"] = bool(find_instruction_sites(scan_region,
                                                        DITHER_REPLACEMENT))
        info["no_aa"] = bool(find_instruction_sites(scan_region, AA_REPLACEMENT))

        vi_tables_320 = find_vi_tables(full_be, WIDTH_320_DATA)
        vi_tables_640 = find_vi_tables(full_be, WIDTH_640_DATA)

    info["vi_table_count"] = len(vi_tables_320)
    info["vi_table_640_count"] = len(vi_tables_640)
    # Strict on purpose: this drives the "already hi-res, skip it" decision,
    # and a ROM with 640 *and* 320 tables still has work left. The mixed
    # case gets its own flag so reports do not have to call it 320x240.
    info["is_hires_640x480"] = len(vi_tables_640) > 0 and len(vi_tables_320) == 0
    info["is_mixed_resolution"] = len(vi_tables_640) > 0 and len(vi_tables_320) > 0

    info["has_subdrag_patch"] = get_subdrag_patch(
        info["crc1"], info["crc2"]) is not None
    info["hires_support"], info["hires_support_reason"] = hires_support(info)

    if with_hashes:
        info["md5"], info["sha1"] = _hash_file(rom_path)

    return info


# ---------------------------------------------------------------------------
# Patch pipeline
# ---------------------------------------------------------------------------

@dataclass
class PatchOptions:
    no_aa: bool = True
    no_dither: bool = True
    no_divot: bool = False
    no_gamma: bool = False
    hires: bool = False
    # The generic VI-table width flip renders incorrectly on hardware (see
    # hires_support). Requesting hi-res on a dump with no verified patch is
    # a no-op unless this is set explicitly.
    force_hires: bool = False


MAX_FILENAME_BYTES = 255  # single name component on ext4/NTFS/APFS


def _filename_byte_len(name):
    enc = sys.getfilesystemencoding() or "utf-8"
    return len(name.encode(enc, "surrogateescape"))


def _fit_base_name(base_fn, suffix):
    """Shorten *base_fn* so `base_fn + suffix` fits one filesystem name
    component. Plain truncation would map two long titles sharing a prefix
    onto the same output name, so a digest of the full stem is appended
    whenever anything is actually cut."""
    if _filename_byte_len(base_fn + suffix) <= MAX_FILENAME_BYTES:
        return base_fn
    digest = "~" + hashlib.sha1(
        base_fn.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    budget = MAX_FILENAME_BYTES - _filename_byte_len(suffix + digest)
    trimmed = base_fn
    while trimmed and _filename_byte_len(trimmed) > budget:
        trimmed = trimmed[:-1]
    return trimmed.rstrip(" _-") + digest


def _numbered_variant(path, n):
    root, ext = os.path.splitext(path)
    return f"{root} ({n}){ext}"


def _free_output_path(path, avoid=None):
    """First variant of *path* ('x.z64', 'x (2).z64', 'x (3).z64', ...)
    that neither exists nor equals *avoid*."""
    avoid_abs = os.path.abspath(avoid) if avoid else None

    def taken(p):
        if avoid_abs is not None and os.path.abspath(p) == avoid_abs:
            return True
        return os.path.exists(p)

    if not taken(path):
        return path
    n = 2
    while taken(_numbered_variant(path, n)):
        n += 1
    return _numbered_variant(path, n)


def build_output_path(rom_path, applied, output_dir=None):
    """Output path with a descriptive tag, next to the input or in
    *output_dir* when given. Never equals the input path and never an
    existing file - originals and earlier results are both preserved, with
    a '(2)', '(3)', ... suffix disambiguating."""
    if "HR" in applied and "NoAA" in applied:
        tag = " [HR+NoAA]"
    elif "HR" in applied:
        tag = " [640p]"
    elif "NoAA" in applied:
        tag = " [NoAA]"
    elif "NoDither" in applied:
        tag = " [NoDither]"
    else:
        tag = " [PATCHED]"

    dir_name, full_fn = os.path.split(rom_path)
    if output_dir:
        dir_name = output_dir
    base_fn, _ = os.path.splitext(full_fn)
    for t in OUTPUT_TAGS:
        if base_fn.endswith(t):
            base_fn = base_fn[:-len(t)]
            break

    suffix = f"{tag}.z64"
    base_fn = _fit_base_name(base_fn, suffix)
    return _free_output_path(os.path.join(dir_name, base_fn + suffix),
                             avoid=rom_path)


def reserve_output_path(rom_path, applied, output_dir=None):
    """build_output_path plus an atomic claim on the name: the file is
    created empty with O_EXCL, so two concurrent batch workers can never
    settle on the same path between choosing it and writing to it."""
    while True:
        candidate = build_output_path(rom_path, applied, output_dir=output_dir)
        os.makedirs(os.path.dirname(candidate) or ".", exist_ok=True)
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue  # lost the race; re-scan picks the next free name
        os.close(fd)
        return candidate


def move_onto_reserved(src, dst):
    """Move *src* over the placeholder created by reserve_output_path.
    os.replace is atomic but same-filesystem only; shutil.move handles the
    cross-device case (output_dir on another drive)."""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(src, dst)


def _files_differ(path_a, path_b, chunk=1024 * 1024):
    """Chunked content comparison; used to tell a real patch from a tool
    that exited cleanly without changing anything."""
    if os.path.getsize(path_a) != os.path.getsize(path_b):
        return True
    with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
        while True:
            ca, cb = fa.read(chunk), fb.read(chunk)
            if ca != cb:
                return True
            if not ca:
                return False


def _temp_dir_for(rom_path, output_dir=None):
    """Where working files go. The destination directory is preferred so
    the final move is a rename rather than a multi-MB copy, but only when
    it is actually writable - read-only mounts and network shares fall
    back to system temp instead of failing outright."""
    for candidate in (output_dir, os.path.dirname(os.path.abspath(rom_path))):
        if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK):
            return candidate
    return tempfile.gettempdir()


def _run_tool(cmd, timeout=SUBPROCESS_TIMEOUT):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          creationflags=CREATE_NO_WINDOW, timeout=timeout)


def patch_rom(rom_path, options, log=print, should_cancel=lambda: False,
              output_dir=None):
    """Patch a single ROM. Returns a result dict:
    {status: patched|skipped|error|cancelled, message, output, applied,
    input}. When *output_dir* is given, the tagged output is written
    there instead of next to the input."""
    result = {"status": "error", "message": "", "output": None,
              "applied": set(), "input": rom_path}

    fd, temp_z64 = tempfile.mkstemp(suffix=".temp.z64",
                                    dir=_temp_dir_for(rom_path, output_dir))
    os.close(fd)
    patched_z64 = temp_z64[: -len(".temp.z64")] + ".patched.z64"
    tools = check_tools()

    def cancelled():
        if should_cancel():
            result["status"] = "cancelled"
            result["message"] = "Cancelled by user"
            return True
        return False

    try:
        if not ensure_z64(rom_path, temp_z64):
            result["status"] = "skipped"
            result["message"] = "Not a recognizable N64 ROM (bad header magic)"
            return result

        if cancelled():
            return result

        info = inspect_rom_details(rom_path)
        applied = result["applied"]
        base = temp_z64            # current working file
        patched_exists = False     # True once `patched_z64` holds working data
        subdrag_used = False

        # --- Stage 1: SubDrag verified .xdelta on the CLEAN source ---------
        if options.hires:
            patch = get_subdrag_patch(info["crc1"], info["crc2"])
            if patch:
                if tools["xdelta3"]:
                    ok, msg = try_subdrag_xdelta(patch, temp_z64, patched_z64)
                    log(f"  SubDrag .xdelta: {msg}")
                    if ok:
                        subdrag_used = True
                        patched_exists = True
                        base = patched_z64
                        applied.add("HR")
                        if patch_includes_noaa(patch):
                            applied.add("NoAA")
                else:
                    log("  SubDrag patch available but no runnable xdelta3 found - using Smart VI engine")

        if cancelled():
            return result

        # --- Stage 2: VI filters (No-AA / dither / divot / gamma) ----------
        want_filters = options.no_aa or options.no_dither or options.no_divot or options.no_gamma
        if want_filters:
            filters_done = False
            if options.no_aa and tools["u64aap"]:
                out_tmp = patched_z64 + ".u64aap_tmp.z64"
                cmd = [U64AAP_PATH, "-i", base, "-o", out_tmp]
                if options.no_dither:
                    cmd.append("-f")
                if options.no_divot:
                    cmd.append("-d")
                if options.no_gamma:
                    cmd.extend(["-g", "-c"])
                try:
                    res = _run_tool(cmd)
                    # Success is "exited clean and actually changed the ROM",
                    # not a phrase in stdout - that string is locale- and
                    # version-dependent. A no-op output means u64aap had no
                    # database entry, which is the fall-through case anyway.
                    if (res.returncode == 0 and os.path.isfile(out_tmp)
                            and os.path.getsize(out_tmp) > 0
                            and _files_differ(out_tmp, base)):
                        os.replace(out_tmp, patched_z64)
                        patched_exists = True
                        base = patched_z64
                        filters_done = True
                        applied.add("NoAA")
                        if options.no_dither:
                            applied.add("NoDither")
                        log("  u64aap.exe: SUCCESS - No-AA patched")
                    else:
                        if os.path.isfile(out_tmp):
                            os.remove(out_tmp)
                        log("  u64aap.exe: not in database, trying dynamic patcher")
                except (subprocess.TimeoutExpired, OSError) as e:
                    if os.path.isfile(out_tmp):
                        os.remove(out_tmp)
                    log(f"  u64aap.exe error: {e} - trying dynamic patcher")
            elif not options.no_aa and (options.no_divot or options.no_gamma):
                log("  Note: divot/gamma removal requires the u64aap AA stage (No-AA is unchecked)")

            if not filters_done and (options.no_aa or options.no_dither):
                if not patched_exists:
                    with open(temp_z64, "rb") as f_in, open(patched_z64, "wb") as f_out:
                        f_out.write(f_in.read())
                    patched_exists = True
                    base = patched_z64
                dyn = apply_dynamic_vi_patch(patched_z64,
                                             no_aa=options.no_aa and not subdrag_used,
                                             no_dither=options.no_dither,
                                             log=log)
                if dyn:
                    applied.update(dyn)
                    log(f"  Dynamic VI matcher: SUCCESS ({', '.join(sorted(dyn))})")
                elif options.no_aa and not subdrag_used:
                    log("  Dynamic VI matcher: no patchable VI instruction masks found")

        if cancelled():
            return result

        # --- Stage 3: Smart Hi-Res fallback --------------------------------
        if options.hires and not subdrag_used:
            support = info.get("hires_support", HIRES_UNSUPPORTED)
            if support == HIRES_NATIVE:
                log("  Hi-Res Engine: SKIPPED - ROM already renders at 640x480")
            elif support == HIRES_UNSUPPORTED and not options.force_hires:
                # Refusing here is the fix for the hardware bug: the generic
                # width flip produced doubled/misplaced output on every ROM.
                log(f"  Hi-Res Engine: NOT SUPPORTED - {info.get('hires_support_reason', '')}")
                log("    Use --force-hires to apply it anyway (expect broken rendering).")
            else:
                if not patched_exists:
                    with open(temp_z64, "rb") as f_in, open(patched_z64, "wb") as f_out:
                        f_out.write(f_in.read())
                    patched_exists = True
                hires_ok, _count, hires_msg = apply_smart_hires_patch(patched_z64)
                if hires_ok:
                    applied.add("HR")
                    label = ("EXPERIMENTAL" if support == HIRES_UNSUPPORTED
                             else "Smart VI Table")
                    log(f"  Hi-Res Engine: SUCCESS ({label}) - {hires_msg}")
                    if support == HIRES_UNSUPPORTED:
                        log("    WARNING: forced on an unverified dump - "
                            "rendering is expected to be wrong on hardware.")
                else:
                    log(f"  Hi-Res Engine: SKIPPED - {hires_msg}")

        # --- Verdict ---------------------------------------------------------
        if not applied:
            if info["is_60fps_or_mod"]:
                reason = "Already optimized! 60fps/Hacks already removed N64 blur"
            elif info["no_aa"] and info["no_dither"]:
                reason = "Already patched with No-AA & No-Dither (no re-patch needed)"
            elif info["is_hires_640x480"] and options.hires:
                reason = "Already 640x480 hi-res (native or previously patched)"
            elif (options.hires
                  and info.get("hires_support") == HIRES_UNSUPPORTED
                  and not options.force_hires):
                reason = (f"640x480 not supported for this dump - "
                          f"{info.get('hires_support_reason', '')}")
            else:
                reason = "ROM contains no patchable VI data (compressed or non-standard)"
            result["status"] = "skipped"
            result["message"] = reason
            return result

        # --- CRC fix + finalize ---------------------------------------------
        crc_done = False
        if tools["rn64crc"]:
            try:
                crc_res = _run_tool([RN64CRC_PATH, "-u", patched_z64])
                # rn64crc exits 0 even when it cannot identify the boot chip
                # and leaves the header untouched ("Unable to calculate!"), so
                # the file is the authority here, not the return code. Getting
                # this wrong ships a ROM that black-screens on hardware.
                if crc_res.returncode == 0 and crc_header_is_valid(patched_z64):
                    crc_done = True
                    log(f"  CRC Update: {crc_res.stdout.strip() or crc_res.stderr.strip()}")
                elif crc_res.returncode == 0:
                    log("  rn64crc exited 0 but left invalid checksums - "
                        "falling back to native engine")
                else:
                    log(f"  rn64crc returned {crc_res.returncode}, falling back to native engine")
            except (subprocess.TimeoutExpired, OSError) as e:
                log(f"  rn64crc failed ({e}), falling back to native engine")
        if not crc_done:
            ok, crc_msg = fix_rom_crc_native(patched_z64)
            if ok:
                log(f"  CRC Update: {crc_msg}")
            else:
                log(f"  WARNING: CRC Update FAILED ({crc_msg}) - boot checksums NOT updated!")

        final_path = reserve_output_path(rom_path, applied, output_dir=output_dir)
        try:
            move_onto_reserved(patched_z64, final_path)
        except Exception:
            # Don't leave the empty placeholder behind for a failed move.
            try:
                os.remove(final_path)
            except OSError:
                pass
            raise
        patched_exists = False

        result["status"] = "patched"
        result["output"] = final_path
        result["message"] = os.path.basename(final_path)
        return result

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
        # The UI shows only the message; the log keeps enough to debug it.
        result["traceback"] = traceback.format_exc()
        try:
            append_log([f"ERROR patching {rom_path}", result["traceback"]])
        except OSError:
            pass
        return result
    finally:
        for p in (temp_z64, patched_z64, patched_z64 + ".u64aap_tmp.z64",
                  patched_z64 + ".xdelta_out.z64"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Post-patch verification
#
# Re-opens a produced file and checks it independently of the code that
# wrote it. Checks are split in two:
#
#   strict   - "will this boot": the image is a recognizable ROM, the CIC
#              is identifiable, and the stored boot checksums recompute to
#              the stored values. A strict failure means a black screen on
#              hardware, so it fails the ROM.
#   advisory - "did the requested effect land": reported but not fatal,
#              because an effect applied through u64aap or a SubDrag delta
#              need not leave the same byte signature the dynamic patcher
#              does, and a missing signature there is not proof of failure.
# ---------------------------------------------------------------------------

def verify_output(path, applied=None):
    """Verify a patched ROM. Returns
    {"ok": bool, "checks": [{"name", "ok", "strict", "detail"}, ...]}
    where *ok* reflects the strict checks only."""
    applied = applied or set()
    checks = []

    def add(name, ok, strict, detail=""):
        checks.append({"name": name, "ok": bool(ok), "strict": strict,
                       "detail": detail})

    def result():
        return {"ok": all(c["ok"] for c in checks if c["strict"]),
                "checks": checks}

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        add("readable", False, True, str(e))
        return result()

    fmt, label = detect_format(data)
    add("format", fmt is not None, True, label)
    if fmt is None:
        return result()
    be = to_big_endian(data, fmt)

    chip = detect_cic_chip(be)
    add("cic", chip is not None, True,
        f"CIC-{chip}" if chip else "boot chip not identifiable")

    if chip is not None:
        crc = calculate_n64_crc(be, chip)
        if crc is None:
            add("crc", False, True, f"no CRC algorithm for CIC-{chip}")
        else:
            stored = (int.from_bytes(be[0x10:0x14], "big"),
                      int.from_bytes(be[0x14:0x18], "big"))
            add("crc", stored == crc, True,
                f"header {stored[0]:08X}/{stored[1]:08X} vs "
                f"computed {crc[0]:08X}/{crc[1]:08X}")

    info = inspect_rom_details(path)
    if "NoAA" in applied:
        add("no_aa", info["no_aa"], False,
            "AA mask signature present" if info["no_aa"]
            else "no AA mask signature (expected when applied via u64aap/xdelta)")
    if "NoDither" in applied:
        add("no_dither", info["no_dither"], False,
            "dither mask signature present" if info["no_dither"]
            else "no dither mask signature (expected when applied via u64aap)")
    if "HR" in applied:
        converted = info["vi_table_count"] == 0 and info["vi_table_640_count"] > 0
        add("hires", converted, False,
            f"{info['vi_table_count']} x320 / {info['vi_table_640_count']} x640 "
            f"VI tables remain")

    return result()


def verify_report_rows(results):
    """Flatten patch results into rows for CSV/JSON export, so a run over a
    real library produces a publishable compatibility matrix without
    shipping any ROM data. Hashes identify the dump; no content is copied."""
    rows = []
    for res in results:
        out = res.get("output")
        if not out:
            continue
        verdict = verify_output(out, res.get("applied"))
        md5, sha1 = _hash_file(res["input"])
        rows.append({
            "input": os.path.basename(res["input"]),
            "input_md5": md5,
            "input_sha1": sha1,
            "output": os.path.basename(out),
            "applied": " ".join(sorted(res.get("applied") or ())),
            "verified": verdict["ok"],
            "failed_checks": " ".join(
                c["name"] for c in verdict["checks"]
                if c["strict"] and not c["ok"]),
            "advisories": " ".join(
                c["name"] for c in verdict["checks"]
                if not c["strict"] and not c["ok"]),
        })
    return rows


# ---------------------------------------------------------------------------
# Report export
# ---------------------------------------------------------------------------

def export_rows(rows, path, keys=None):
    """Write a list of dicts to CSV or JSON, chosen by file extension."""
    if keys is None:
        keys = list(rows[0].keys()) if rows else []
    if path.lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
    else:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
    return path


def export_report(infos, path):
    """Write inspection results to CSV or JSON (chosen by file extension)."""
    keys = ["filename", "path", "size_mb", "format", "title", "game_id", "region",
            "crc1", "crc2", "no_aa", "no_dither", "is_60fps_or_mod",
            "is_hires_640x480", "is_mixed_resolution", "vi_table_count",
            "vi_table_640_count", "has_subdrag_patch", "hires_support",
            "hires_support_reason", "md5", "sha1"]
    rows = [{k: info.get(k, "") for k in keys} for info in infos]
    return export_rows(rows, path, keys)
