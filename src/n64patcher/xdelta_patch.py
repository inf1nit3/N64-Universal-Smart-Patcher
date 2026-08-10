"""
xdelta_patch.py
Pure-Python VCDIFF (RFC 3284 / xdelta3) decoder - the built-in fallback
engine for SubDrag `.xdelta` community patches when no external xdelta3
binary is runnable (e.g. macOS/Linux without a system install, where the
bundled Windows .exe cannot execute).

Implements the VCDIFF subset that xdelta3 emits by default and that all
bundled hi-res patches use:

  - D6 C3 C4 00 magic, optional application header (skipped)
  - multiple target windows, VCD_SOURCE copy windows
  - default RFC 3284 code table (near modes = 4, same modes = 3)
  - Adler-32 verification of every decoded target window

Not supported (raises XdeltaPatchError with a clear message):

  - secondary compression (VCD_DECOMPRESS) - none of the bundled patches
    use it, so the fallback stays small and safe
  - custom code tables (VCD_CODETABLE)
  - VCD_TARGET windows (the xdelta3 encoder never produces them)

Pure standard library, mirroring the project's pure-Python CRC engine,
so frozen builds and CI runners work everywhere without extra deps.
"""

import os
import zlib
from typing import Any, Dict, Iterator, List, Tuple

VCDIFF_MAGIC = b"\xd6\xc3\xc4\x00"

# Window indicator bits
VCD_SOURCE = 1
VCD_TARGET = 2
VCD_ADLER32 = 4
VCD_INVWIN = 0xF8  # any other bits set in the window indicator are invalid

# File-header / delta indicator bits
VCD_DECOMPRESS = 1
VCD_CODETABLE = 2
VCD_APPHEADER = 4

# Instruction types (xdelta3 numbering)
XD3_NOOP = 0
XD3_ADD = 1
XD3_RUN = 2
XD3_CPY = 3  # copy modes are encoded as XD3_CPY + mode

# Default RFC 3284 code table parameters (xdelta3 defaults)
MIN_MATCH = 4
ADD_SIZES = 17
NEAR_MODES = 4
SAME_MODES = 3
CPY_SIZES = 15
ADDCOPY_ADD_MAX = 4
ADDCOPY_NEAR_CPY_MAX = 6
ADDCOPY_SAME_CPY_MAX = 4
COPYADD_ADD_MAX = 1
COPYADD_NEAR_CPY_MAX = 4
COPYADD_SAME_CPY_MAX = 4

# Sanity cap: the largest licensed N64 ROM is 64 MB.
MAX_WINDOW_SIZE = 128 * 1024 * 1024


class XdeltaPatchError(ValueError):
    """Raised for malformed or unsupported VCDIFF/xdelta input."""


# ---------------------------------------------------------------------------
# VCDIFF integer encoding (base-128, big-endian groups, MSB = continue)
# ---------------------------------------------------------------------------

def _read_int(data: bytes, pos: int) -> Tuple[int, int]:
    """Read one VCDIFF varint. Returns (value, new_pos)."""
    value = 0
    n = len(data)
    while True:
        if pos >= n:
            raise XdeltaPatchError("unexpected end of patch data (varint)")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
        if value > 1 << 56:
            raise XdeltaPatchError("varint overflow (corrupt patch)")


def encode_size(value: int) -> bytes:
    """Encode one VCDIFF varint (used by the test suite to build
    hand-crafted vectors)."""
    if value < 0:
        raise ValueError("VCDIFF integers are unsigned")
    if value == 0:
        return b"\x00"
    groups: List[int] = []
    while value:
        groups.append(value & 0x7F)
        value >>= 7
    groups.reverse()
    return bytes(g | 0x80 for g in groups[:-1]) + bytes([groups[-1]])


# ---------------------------------------------------------------------------
# Default RFC 3284 code table
# ---------------------------------------------------------------------------

def _build_default_code_table() -> List[Tuple[int, int, int, int]]:
    """Build the 256-entry RFC 3284 default code table exactly like
    xdelta3's xd3_build_code_table(). Each entry is
    (type1, size1, type2, size2); a type of XD3_NOOP means "no second
    instruction". Copy types are XD3_CPY + mode with modes
    0 = VCD_SELF, 1 = VCD_HERE, 2..5 = NEAR, 6..8 = SAME."""
    cpy_modes = 2 + NEAR_MODES + SAME_MODES
    table: List[Tuple[int, int, int, int]] = []

    table.append((XD3_RUN, 0, XD3_NOOP, 0))
    table.append((XD3_ADD, 0, XD3_NOOP, 0))
    for size1 in range(1, ADD_SIZES + 1):
        table.append((XD3_ADD, size1, XD3_NOOP, 0))

    for mode in range(cpy_modes):
        table.append((XD3_CPY + mode, 0, XD3_NOOP, 0))
        for size1 in range(MIN_MATCH, MIN_MATCH + CPY_SIZES):
            table.append((XD3_CPY + mode, size1, XD3_NOOP, 0))

    for mode in range(cpy_modes):
        cpy_max = (ADDCOPY_NEAR_CPY_MAX if mode < 2 + NEAR_MODES
                   else ADDCOPY_SAME_CPY_MAX)
        for size1 in range(1, ADDCOPY_ADD_MAX + 1):
            for size2 in range(MIN_MATCH, cpy_max + 1):
                table.append((XD3_ADD, size1, XD3_CPY + mode, size2))

    for mode in range(cpy_modes):
        cpy_max = (COPYADD_NEAR_CPY_MAX if mode < 2 + NEAR_MODES
                   else COPYADD_SAME_CPY_MAX)
        for size1 in range(MIN_MATCH, cpy_max + 1):
            for size2 in range(1, COPYADD_ADD_MAX + 1):
                table.append((XD3_CPY + mode, size1, XD3_ADD, size2))

    if len(table) != 256:
        raise XdeltaPatchError(f"internal: code table has {len(table)} entries")
    return table


CODE_TABLE = _build_default_code_table()


# ---------------------------------------------------------------------------
# Structural parsing (also used for inspection without a source ROM)
# ---------------------------------------------------------------------------

def iter_windows(patch_data: bytes) -> Iterator[Dict[str, Any]]:
    """Yield one dict per window with all header fields plus the three
    section payloads (data/inst/addr). Validates the structure and raises
    XdeltaPatchError on malformed or unsupported input."""
    if len(patch_data) < 5 or patch_data[:4] != VCDIFF_MAGIC:
        raise XdeltaPatchError("not a VCDIFF/xdelta file (bad magic)")

    pos = 4
    hdr_ind = patch_data[pos]
    pos += 1
    if hdr_ind & VCD_DECOMPRESS:
        raise XdeltaPatchError("secondary compression is not supported")
    if hdr_ind & VCD_CODETABLE:
        raise XdeltaPatchError("custom code tables are not supported")
    if hdr_ind & VCD_APPHEADER:
        ah_len, pos = _read_int(patch_data, pos)
        if pos + ah_len > len(patch_data):
            raise XdeltaPatchError("truncated application header")
        pos += ah_len

    n = len(patch_data)
    while pos < n:
        win_ind = patch_data[pos]
        pos += 1
        if win_ind & VCD_INVWIN:
            raise XdeltaPatchError(
                f"invalid window indicator bits: {win_ind:#04x}")
        if win_ind & VCD_TARGET:
            raise XdeltaPatchError("VCD_TARGET windows are not supported")

        cpy_len = cpy_off = 0
        if win_ind & VCD_SOURCE:
            cpy_len, pos = _read_int(patch_data, pos)
            cpy_off, pos = _read_int(patch_data, pos)

        enc_len, pos = _read_int(patch_data, pos)
        body_start = pos

        tgt_len, pos = _read_int(patch_data, pos)
        if tgt_len > MAX_WINDOW_SIZE:
            raise XdeltaPatchError(
                f"window size {tgt_len} exceeds sanity limit")

        if pos >= n:
            raise XdeltaPatchError("truncated window header")
        delta_ind = patch_data[pos]
        pos += 1
        if delta_ind != 0:
            raise XdeltaPatchError(
                "delta indicator set (secondary compression) - not supported")

        data_len, pos = _read_int(patch_data, pos)
        inst_len, pos = _read_int(patch_data, pos)
        addr_len, pos = _read_int(patch_data, pos)

        adler32 = None
        if win_ind & VCD_ADLER32:
            if pos + 4 > n:
                raise XdeltaPatchError("truncated window checksum")
            adler32 = int.from_bytes(patch_data[pos:pos + 4], "big")
            pos += 4

        if pos + data_len + inst_len + addr_len > n:
            raise XdeltaPatchError("truncated window sections")
        data = patch_data[pos:pos + data_len]
        pos += data_len
        inst = patch_data[pos:pos + inst_len]
        pos += inst_len
        addr = patch_data[pos:pos + addr_len]
        pos += addr_len

        if pos != body_start + enc_len:
            raise XdeltaPatchError("window length mismatch (corrupt patch)")

        yield {
            "win_ind": win_ind,
            "cpy_len": cpy_len,
            "cpy_off": cpy_off,
            "tgt_len": tgt_len,
            "adler32": adler32,
            "data": data,
            "inst": inst,
            "addr": addr,
        }


# ---------------------------------------------------------------------------
# Window replay
# ---------------------------------------------------------------------------

def _replay_window(win: Dict[str, Any], source: bytes) -> bytes:
    """Replay one window's instruction stream against the source file and
    return the decoded target window bytes."""
    cpy_len = win["cpy_len"]
    cpy_off = win["cpy_off"]
    if win["win_ind"] & VCD_SOURCE:
        need = cpy_off + cpy_len
        if need > len(source):
            raise XdeltaPatchError(
                f"source ROM too short for this patch (needs {need} bytes, "
                f"got {len(source)} - wrong ROM version/region?)")

    inst = win["inst"]
    data = win["data"]
    addr = win["addr"]
    out = bytearray()

    near = [0] * NEAR_MODES
    next_slot = 0
    same = [0] * (SAME_MODES * 256)
    same_start = 2 + NEAR_MODES

    ip = dp = ap = 0
    here = cpy_len  # decoder position in copy-window + target space

    while ip < len(inst):
        entry = CODE_TABLE[inst[ip]]
        ip += 1
        for half in (0, 1):
            typ = entry[0] if half == 0 else entry[2]
            size = entry[1] if half == 0 else entry[3]
            if typ == XD3_NOOP:
                continue

            if size == 0:
                size, ip = _read_int(inst, ip)

            if typ == XD3_ADD:
                if dp + size > len(data):
                    raise XdeltaPatchError("data section underflow")
                out += data[dp:dp + size]
                dp += size
            elif typ == XD3_RUN:
                if dp + 1 > len(data):
                    raise XdeltaPatchError("data section underflow")
                out += data[dp:dp + 1] * size
                dp += 1
            else:  # COPY
                mode = typ - XD3_CPY
                if mode < same_start:
                    d, ap = _read_int(addr, ap)
                    if mode == 0:              # VCD_SELF
                        a = d
                    elif mode == 1:            # VCD_HERE
                        a = here - d
                    else:                      # VCD_NEAR
                        a = near[mode - 2] + d
                else:                          # VCD_SAME
                    if ap >= len(addr):
                        raise XdeltaPatchError("address section underflow")
                    a = same[(mode - same_start) * 256 + addr[ap]]
                    ap += 1

                # Update the address cache exactly like xdelta3 does.
                near[next_slot] = a
                next_slot = (next_slot + 1) % NEAR_MODES
                same[a % (SAME_MODES * 256)] = a

                if a < 0 or a >= here:
                    raise XdeltaPatchError("copy address out of range")
                if a < cpy_len and a + size > cpy_len:
                    raise XdeltaPatchError("copy crosses source window end")

                if a < cpy_len:
                    out += source[cpy_off + a:cpy_off + a + size]
                else:
                    o = a - cpy_len
                    if o + size <= len(out):
                        out += out[o:o + size]
                    else:  # overlapping forward copy
                        for i in range(size):
                            out.append(out[o + i])

            here += size

    if len(out) != win["tgt_len"]:
        raise XdeltaPatchError("decoded window size mismatch (corrupt patch)")
    return bytes(out)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decode_xdelta(patch_data: bytes, source_data: bytes,
                  verify_adler: bool = True) -> bytes:
    """Decode a VCDIFF/xdelta3 delta against the source bytes and return
    the full reconstructed target. Raises XdeltaPatchError on malformed
    input, unsupported features or checksum/size mismatches."""
    out = bytearray()
    for win in iter_windows(patch_data):
        decoded = _replay_window(win, source_data)
        if verify_adler and win["adler32"] is not None:
            actual = zlib.adler32(decoded) & 0xFFFFFFFF
            if actual != win["adler32"]:
                raise XdeltaPatchError(
                    f"Adler-32 mismatch (expected {win['adler32']:08X}, got "
                    f"{actual:08X}) - the source ROM does not match this "
                    f"patch (wrong version/region?)")
        out += decoded
    return bytes(out)


def apply_xdelta_patch(rom_path: str, patch_path: str,
                       output_path: str) -> Dict[str, Any]:
    """File-level API in the same result-dict style as ips_bps_patcher:
    {'status': 'patched'|'error', 'message': ..., 'output': ...}."""
    if not os.path.isfile(rom_path) or not os.path.isfile(patch_path):
        return {"status": "error", "message": "Source ROM or patch file missing"}
    try:
        with open(patch_path, "rb") as f:
            patch_data = f.read()
        with open(rom_path, "rb") as f:
            source_data = f.read()
        target = decode_xdelta(patch_data, source_data)
        with open(output_path, "wb") as f:
            f.write(target)
        return {
            "status": "patched",
            "message": (f"xdelta patch applied (built-in VCDIFF engine, "
                        f"{len(target)} bytes written)"),
            "output": output_path,
        }
    except XdeltaPatchError as e:
        return {"status": "error", "message": f"xdelta patch error: {e}"}
    except OSError as e:
        return {"status": "error", "message": f"xdelta patch I/O error: {e}"}
