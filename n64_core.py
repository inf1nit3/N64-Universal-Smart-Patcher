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
  5. Recalculate boot checksums (rn64crc) and write a new output file;
     originals are never modified
"""

import csv
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

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


U64AAP_PATH = get_asset_path("N64noAAPatcher", "additionals", "u64aap.exe")
RN64CRC_PATH = get_asset_path("N64noAAPatcher", "additionals", "rn64crc.exe")
XDELTA3_PATH = get_asset_path("N64noAAPatcher", "additionals", "xdelta3.exe")
HIRES_PATCHES_DIR = get_asset_path("N64noAAPatcher", "hires_patches")

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SUBPROCESS_TIMEOUT = 120  # seconds; u64aap/xdelta/rn64crc are all fast

ROM_EXTENSIONS = (".z64", ".n64", ".v64")
OUTPUT_TAGS = (" [HR+NoAA]", " [640p]", " [NoAA]", " [NoDither]", " [PATCHED]")
TEMP_SUFFIXES = (".temp.z64", ".patched.z64", ".xdelta_out.z64")


def get_log_path():
    """Per-user log location (EXE_DIR is read-only for installed bundles)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        log_dir = os.path.join(base, "N64SmartPatcher")
    else:
        log_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "n64-smart-patcher")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "N64_Patcher_Log.txt")


def append_log(lines):
    path = get_log_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def check_tools():
    """Availability of the bundled external helpers. All stages degrade
    gracefully when a tool is missing."""
    return {
        "u64aap": os.path.isfile(U64AAP_PATH),
        "rn64crc": os.path.isfile(RN64CRC_PATH),
        "xdelta3": os.path.isfile(XDELTA3_PATH),
        "hires_patches": os.path.isdir(HIRES_PATCHES_DIR),
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
# ---------------------------------------------------------------------------

def apply_dynamic_vi_patch(z64_path, no_aa=True, no_dither=True):
    """Apply instruction-mask patches. Each option is honored independently.
    Returns the set of applied labels ('NoAA', 'NoDither')."""
    with open(z64_path, "rb") as f:
        data = bytearray(f.read())

    applied = set()

    if no_dither:
        pattern = bytes.fromhex("31cf0040")
        pos = 0
        while True:
            pos = data.find(pattern, pos)
            if pos == -1:
                break
            data[pos:pos + 4] = bytes.fromhex("31cf0000")
            if data[pos + 4:pos + 8] == bytes.fromhex("11e0000d"):
                data[pos + 4:pos + 8] = bytes.fromhex("1000000d")
            applied.add("NoDither")
            pos += 4

    if no_aa:
        pattern = bytes.fromhex("30423000")
        pos = 0
        while True:
            pos = data.find(pattern, pos)
            if pos == -1:
                break
            data[pos:pos + 4] = bytes.fromhex("30422000")
            applied.add("NoAA")
            pos += 4

    if applied:
        with open(z64_path, "wb") as f:
            f.write(data)
    return applied


# ---------------------------------------------------------------------------
# SubDrag verified .xdelta patches (matched by ROM internal title)
# ---------------------------------------------------------------------------

SUBDRAG_PATCHES = {
    "SUPER MARIO 64":   "Super Mario 64 (U) [!] 640 x 480i No AA[SubDrag].xdelta",
    "GOLDENEYE":        "GE640x480iEnhanced[SubDragTrevorZoinkity].xdelta",
    "BANJO KAZOOIE":    "Banjo-Kazooie (U) (V1.1) 640 x 480i NoAA[SubDrag].xdelta",
    "F-ZERO X":         "F-ZERO X (U) 640x480i No AA[SubDrag].xdelta",
    "FORSAKEN 64":      "Forsaken 64 (U) 640x480i NoAA [SubDrag].xdelta",
    "POKEMON SNAP":     "PokemonSnap640x480iNoAA.xdelta",
    "QUAKE II":         "Quake II (U) [!] 640 x 480i NoAA[SubDrag].xdelta",
    "GOLDEN NUGGET 64": "GoldenNugget 640 x 480i CrapsCrashes[SubDrag].xdelta",
}


def get_subdrag_patch_for_title(title):
    """Return the path of a usable SubDrag patch for this title, or None."""
    if not os.path.isdir(HIRES_PATCHES_DIR):
        return None
    title_upper = (title or "").upper().strip()
    for key, filename in SUBDRAG_PATCHES.items():
        if key in title_upper:
            candidate = os.path.join(HIRES_PATCHES_DIR, filename)
            if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                return candidate
    return None


def patch_includes_noaa(patch_path):
    return "noaa" in os.path.basename(patch_path).lower().replace(" ", "").replace("-", "")


def try_subdrag_xdelta(patch_file, source_z64, output_z64):
    """Apply a SubDrag .xdelta patch. The source MUST be the pristine ROM -
    xdelta deltas are built against clean dumps and fail on modified data."""
    if not os.path.isfile(XDELTA3_PATH):
        return False, "xdelta3.exe not found"
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

# Heuristic AA/dither status patterns only live in executable code, which
# sits in the first few MB. Capping the scan avoids false positives from
# compressed asset data later in the ROM.
AA_SCAN_LIMIT = 8 * 1024 * 1024


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
        "crc1": "Unknown",
        "crc2": "Unknown",
        "no_aa": False,
        "no_dither": False,
        "is_60fps_or_mod": False,
        "is_hires_640x480": False,
        "vi_table_count": 0,
        "has_subdrag_patch": False,
    }

    fn_lower = os.path.basename(rom_path).lower()
    if "60_fps" in fn_lower or "60fps" in fn_lower or "redux" in fn_lower:
        info["is_60fps_or_mod"] = True

    with open(rom_path, "rb") as f:
        full_bytes = f.read()

    fmt, label = detect_format(full_bytes)
    info["format"] = label
    if fmt is None or len(full_bytes) < 64:
        return info

    full_be = to_big_endian(full_bytes, fmt)

    info["title"] = full_be[32:52].decode("ascii", errors="ignore").strip()
    info["game_id"] = full_be[59:63].decode("ascii", errors="ignore").strip()
    info["crc1"] = full_be[16:20].hex().upper()
    info["crc2"] = full_be[20:24].hex().upper()

    country_byte = full_be[62]
    country_code = chr(country_byte) if 32 <= country_byte < 127 else "?"
    info["region"] = REGION_MAP.get(country_code, f"Unknown ({country_code})")

    scan_region = full_be[:AA_SCAN_LIMIT]
    info["no_dither"] = b"\x31\xcf\x00\x00" in scan_region
    info["no_aa"] = b"\x30\x42\x20\x00" in scan_region or info["no_dither"]

    vi_tables_320 = find_vi_tables(full_be, WIDTH_320_DATA)
    vi_tables_640 = find_vi_tables(full_be, WIDTH_640_DATA)
    info["vi_table_count"] = len(vi_tables_320)
    info["is_hires_640x480"] = len(vi_tables_640) > 0 and len(vi_tables_320) == 0

    info["has_subdrag_patch"] = get_subdrag_patch_for_title(info["title"]) is not None

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
    applied_tags: set = field(default_factory=set)


def build_output_path(rom_path, applied):
    """Output path next to the input with a descriptive tag. Never equals
    the input path (originals are preserved)."""
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
    base_fn, _ = os.path.splitext(full_fn)
    for t in OUTPUT_TAGS:
        if base_fn.endswith(t):
            base_fn = base_fn[:-len(t)]
            break

    max_base_len = 65 - len(tag)
    if len(base_fn) > max_base_len:
        base_fn = base_fn[:max_base_len].rstrip(" _-")

    final_path = os.path.join(dir_name, f"{base_fn}{tag}.z64")
    if os.path.abspath(final_path) == os.path.abspath(rom_path):
        final_path = os.path.join(dir_name, f"{base_fn}{tag} (2).z64")
    return final_path


def _run_tool(cmd, timeout=SUBPROCESS_TIMEOUT):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          creationflags=CREATE_NO_WINDOW, timeout=timeout)


def patch_rom(rom_path, options, log=print, should_cancel=lambda: False):
    """Patch a single ROM. Returns a result dict:
    {status: patched|skipped|error|cancelled, message, output, applied}."""
    result = {"status": "error", "message": "", "output": None, "applied": set()}
    dir_name = os.path.dirname(os.path.abspath(rom_path)) or "."

    fd, temp_z64 = tempfile.mkstemp(suffix=".temp.z64", dir=dir_name)
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
            patch = get_subdrag_patch_for_title(info["title"])
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
                    log("  SubDrag patch available but xdelta3.exe missing - using Smart VI engine")

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
                    if os.path.isfile(out_tmp) and "result: file patched!" in res.stdout:
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
                                             no_dither=options.no_dither)
                if dyn:
                    applied.update(dyn)
                    log(f"  Dynamic VI matcher: SUCCESS ({', '.join(sorted(dyn))})")
                elif options.no_aa and not subdrag_used:
                    log("  Dynamic VI matcher: no patchable VI instruction masks found")

        if cancelled():
            return result

        # --- Stage 3: Smart Hi-Res fallback --------------------------------
        if options.hires and not subdrag_used:
            if not patched_exists:
                with open(temp_z64, "rb") as f_in, open(patched_z64, "wb") as f_out:
                    f_out.write(f_in.read())
                patched_exists = True
            hires_ok, _count, hires_msg = apply_smart_hires_patch(patched_z64)
            if hires_ok:
                applied.add("HR")
                log(f"  Hi-Res Engine: SUCCESS (Smart VI Table) - {hires_msg}")
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
            else:
                reason = "ROM contains no patchable VI data (compressed or non-standard)"
            result["status"] = "skipped"
            result["message"] = reason
            return result

        # --- CRC fix + finalize ---------------------------------------------
        if tools["rn64crc"]:
            try:
                crc_res = _run_tool([RN64CRC_PATH, patched_z64, "-u"])
                log(f"  CRC Update: {crc_res.stdout.strip() or crc_res.stderr.strip()}")
            except (subprocess.TimeoutExpired, OSError) as e:
                log(f"  CRC Update FAILED ({e}) - boot checksums NOT updated!")
        else:
            log("  WARNING: rn64crc.exe missing - boot checksums NOT updated; ROM may black-screen")

        final_path = build_output_path(rom_path, applied)
        os.replace(patched_z64, final_path)
        patched_exists = False

        result["status"] = "patched"
        result["output"] = final_path
        result["message"] = os.path.basename(final_path)
        return result

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
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
# Report export
# ---------------------------------------------------------------------------

def export_report(infos, path):
    """Write inspection results to CSV or JSON (chosen by file extension)."""
    keys = ["filename", "path", "size_mb", "format", "title", "game_id", "region",
            "crc1", "crc2", "no_aa", "no_dither", "is_60fps_or_mod",
            "is_hires_640x480", "vi_table_count", "has_subdrag_patch", "md5", "sha1"]
    rows = [{k: info.get(k, "") for k in keys} for info in infos]
    if path.lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
    else:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
    return path
