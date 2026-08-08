#!/usr/bin/env python3
"""End-to-end smoke test of the INSTALLED command line tool.

The unit suite imports the package; this runs the real `n64patcher` entry
point as a subprocess against real files on disk. That difference is the
whole point - packaging bugs (a missing data file, a resource path that
only resolves on one platform, an entry point that is not on PATH) pass
every unit test and then fail on a user's machine.

Uses synthetic ROM images only; no copyrighted game data is involved.

    python scripts/smoke_test.py                    # the installed package
    python scripts/smoke_test.py dist/n64patcher    # a frozen binary

Exits non-zero if any check fails, printing every failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

# By default the CLI is invoked through the interpreter running this script,
# so a venv does not have to be on PATH. Pass a path to test a frozen binary
# instead - the only way to catch a bundled resource that did not get packed.
# os.path.abspath so a relative "dist/n64patcher" works: subprocess does not
# search the current directory on Windows the way a shell does.
CLI = ([os.path.abspath(sys.argv[1])] if len(sys.argv) > 1
       else [sys.executable, "-m", "n64patcher"])

# This script echoes the tool's own output, which contains emoji. Without
# this, the reporter crashes on a cp1252 or ASCII stdout before it can tell
# you which check failed.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

failures: list[str] = []
checks = 0


def check(condition: bool, description: str, detail: str = "") -> bool:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {description}")
        return True
    print(f"  FAIL {description}")
    if detail:
        print("       " + detail.replace("\n", "\n       "))
    failures.append(description)
    return False


def run(*args: str, expect_ok: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(CLI + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if expect_ok and proc.returncode != 0:
        print(f"  FAIL command failed: n64patcher {' '.join(args)}")
        print("       stdout: " + proc.stdout[-2000:])
        print("       stderr: " + proc.stderr[-2000:])
        failures.append(f"command failed: {' '.join(args)}")
    return proc


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_rom(path: str, size: int = 0x200000, swapped: bool = False) -> str:
    """A minimal but structurally valid big-endian z64 image, carrying the
    VI instruction patterns the dynamic patcher looks for so that a patch
    run has something real to do."""
    rom = bytearray(size)
    rom[0:4] = bytes.fromhex("80371240")
    rom[4:8] = struct.pack(">I", 0x0000000F)     # clock rate
    rom[8:12] = struct.pack(">I", 0x80000400)    # entry point
    rom[12:16] = struct.pack(">I", 0x00001444)   # release
    rom[16:20] = struct.pack(">I", 0xDEADBEEF)   # CRC1
    rom[20:24] = struct.pack(">I", 0x12345678)   # CRC2
    rom[32:52] = b"SMOKE TEST ROM".ljust(20, b" ")
    rom[59:61] = b"NT"
    rom[62:63] = b"E"

    # Filler that is not all zeroes, so "the patcher changed something" is a
    # meaningful statement rather than an artefact of a blank file.
    for offset in range(0x40, 0x1000, 4):
        rom[offset:offset + 4] = struct.pack(">I", (offset * 2654435761) & 0xFFFFFFFF)

    # A VI mode table: width 320 followed by the NTSC burst constant.
    rom[0x1000:0x1004] = bytes.fromhex("00000140")
    rom[0x1004:0x1008] = bytes.fromhex("03E52239")

    # Dither and AA instruction masks inside the code region.
    rom[0x2000:0x2004] = bytes.fromhex("31cf0040")   # andi $t7, $t6, 0x40
    rom[0x2004:0x2008] = bytes.fromhex("11e0000d")   # beq  $t7, $zero, +0xd
    rom[0x2100:0x2104] = bytes.fromhex("30423000")   # andi $v0, $v0, 0x3000

    if swapped:
        # .v64 byte order: swap every 16-bit pair.
        rom[:] = b"".join(rom[i + 1:i + 2] + rom[i:i + 1]
                          for i in range(0, len(rom), 2))
    with open(path, "wb") as f:
        f.write(rom)
    return path


def section(title: str) -> None:
    print(f"\n== {title}")


def main() -> int:
    print(f"platform: {sys.platform}   python: {sys.version.split()[0]}")
    print(f"under test: {' '.join(CLI)}")

    tmp = tempfile.mkdtemp(prefix="n64patcher-smoke-")
    try:
        section("entry point")
        proc = run("--version")
        check(proc.stdout.strip() != "", "--version prints something",
              repr(proc.stdout))

        section("bundled data files resolve")
        proc = run("--list-patches")
        # This is the check that caught the frozen-build bug twice: the
        # package imports fine while shipping zero recipes.
        check("640x480" in proc.stdout or "hires" in proc.stdout.lower(),
              "--list-patches finds the bundled recipes", proc.stdout[:800])
        check(proc.stdout.count("\n") > 5,
              "--list-patches lists more than a couple of entries")

        proc = run("--list-presets")
        check(proc.stdout.strip() != "", "--list-presets prints the presets")

        section("inspect a synthetic ROM")
        rom = make_rom(os.path.join(tmp, "Smoke Test (U) [!].z64"))
        report = os.path.join(tmp, "report.json")
        proc = run(rom, "--inspect-only", "--export", report)
        check(os.path.isfile(report), "--export wrote the report")
        if os.path.isfile(report):
            with open(report, encoding="utf-8") as f:
                rows = json.load(f)
            check(len(rows) == 1, f"report has one row (got {len(rows)})")
            row = rows[0] if rows else {}
            check("z64" in str(row.get("format", "")),
                  "format detected as z64", repr(row.get("format")))
            check("hires_support" in row, "report carries hires_support")
            # An unknown dump has no verified delta, so the honest answer is
            # "unsupported" - the gate added after the hardware bug report.
            check(row.get("hires_support") == "unsupported",
                  "unknown dump is classified unsupported",
                  repr(row.get("hires_support")))

        section("byte order conversion")
        v64 = make_rom(os.path.join(tmp, "Smoke Test (U).v64"), swapped=True)
        proc = run(v64, "--inspect-only")
        check("v64" in proc.stdout, "byte-swapped image detected as v64",
              proc.stdout[:600])

        section("patch and verify")
        outdir = os.path.join(tmp, "out")
        os.makedirs(outdir, exist_ok=True)
        before = sha256(rom)
        # No --verify here: a synthetic image has no real CIC bootcode, so the
        # verifier correctly refuses it. That path is covered separately below.
        proc = run(rom, "--no-dither", "-o", outdir)
        produced = [f for f in os.listdir(outdir) if f.lower().endswith(".z64")]
        check(len(produced) == 1, f"one output produced (got {produced})")
        check(sha256(rom) == before, "the source ROM was not modified in place")
        if produced:
            out = os.path.join(outdir, produced[0])
            check(sha256(out) != before, "output differs from the source")
            check(os.path.getsize(out) == os.path.getsize(rom),
                  "output is the same size as the source")

        section("verifier runs and reports honestly")
        # It must fail here - the synthetic ROM has no identifiable CIC - and
        # say so rather than passing everything it cannot check.
        proc = run(rom, "--no-dither", "-o", os.path.join(tmp, "vfy"),
                   "--verify", expect_ok=False)
        check(proc.returncode != 0,
              "--verify exits non-zero when a check fails")
        check("cic" in proc.stdout.lower(),
              "--verify names the failing check", proc.stdout[-600:])

        section("hi-res gate refuses an unverified dump")
        gate_dir = os.path.join(tmp, "gate")
        os.makedirs(gate_dir, exist_ok=True)
        proc = run(rom, "--hires", "-o", gate_dir)
        check("NOT SUPPORTED" in proc.stdout,
              "the 640x480 request is refused for an unverified dump",
              proc.stdout[-800:])
        widened = [f for f in os.listdir(gate_dir)
                   if "[HR" in f or "[640p]" in f]
        check(widened == [], "no hi-res output was produced", repr(widened))
        # The width word must still read 320 in whatever did get produced.
        for name in os.listdir(gate_dir):
            if not name.lower().endswith(".z64"):
                continue
            with open(os.path.join(gate_dir, name), "rb") as f:
                f.seek(0x1000)
                width = f.read(4)
            check(width == bytes.fromhex("00000140"),
                  f"VI width left at 320 in {name}", width.hex())

        section("manifest round trip")
        man_dir = os.path.join(tmp, "man")
        os.makedirs(man_dir, exist_ok=True)
        run(rom, "--no-dither", "-o", man_dir, "--manifest")
        outputs = [f for f in os.listdir(man_dir) if f.lower().endswith(".z64")]
        manifests = [f for f in os.listdir(man_dir) if f.endswith(".json")]
        check(len(manifests) == 1, f"a manifest was written (got {manifests})")
        if outputs and manifests:
            patched = os.path.join(man_dir, outputs[0])
            proc = run("--show-manifest", os.path.join(man_dir, manifests[0]))
            check(proc.stdout.strip() != "", "--show-manifest describes the patch")
            run("--revert", patched)
            reverted = [os.path.join(man_dir, f) for f in os.listdir(man_dir)
                        if f.lower().endswith(".z64")
                        and os.path.join(man_dir, f) != patched]
            check(any(sha256(p) == before for p in reverted),
                  "--revert reproduced the original bytes exactly",
                  f"candidates: {[os.path.basename(p) for p in reverted]}")

        section("patch creation and application")
        target = os.path.join(tmp, "target.z64")
        shutil.copyfile(rom, target)
        with open(target, "r+b") as f:
            f.seek(0x3000)
            f.write(b"\xde\xad\xc0\xde")
        bps = os.path.join(tmp, "smoke.bps")
        run("--create-patch", rom, target, bps)
        check(os.path.isfile(bps) and os.path.getsize(bps) > 0,
              "--create-patch wrote a .bps file")

        if os.path.isfile(bps):
            apply_dir = os.path.join(tmp, "applied")
            os.makedirs(apply_dir, exist_ok=True)
            run(rom, "--patch-file", bps, "-o", apply_dir)
            applied = [os.path.join(apply_dir, f) for f in os.listdir(apply_dir)
                       if f.lower().endswith(".z64")]
            check(any(sha256(p) == sha256(target) for p in applied),
                  "applying the generated patch reproduces the target exactly",
                  f"produced: {[os.path.basename(p) for p in applied]}")

        section("batch over a folder")
        batch = os.path.join(tmp, "batch")
        os.makedirs(batch, exist_ok=True)
        for i in range(3):
            make_rom(os.path.join(batch, f"Batch Game {i}.z64"), size=0x100000)
        batch_out = os.path.join(tmp, "batch-out")
        os.makedirs(batch_out, exist_ok=True)
        proc = run(batch, "-r", "--no-dither", "-o", batch_out, "-j", "2")
        done = [f for f in os.listdir(batch_out) if f.lower().endswith(".z64")]
        check(len(done) == 3, f"all three ROMs produced output (got {len(done)})")
        check(len(set(done)) == 3, "output names did not collide")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("\nfailed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
