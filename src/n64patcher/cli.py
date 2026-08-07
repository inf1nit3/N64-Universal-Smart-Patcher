"""
n64patcher.cli - command line interface
Universal N64 ROM Inspector & Smart Patcher - headless CLI

Notes on semantics:
  --patch-file applies a community .ips/.bps patch to each CLEAN ROM
  (community patches are built against pristine dumps) and skips the
  engine pipeline for that run.
  --output-dir/-o places every generated file in that directory.
  --fix-crc additionally produces a CRC-repaired [CRCFIX] copy for ROMs
  the engine skipped (flashcart repair use case); patched outputs always
  get corrected boot checksums from the pipeline itself.
"""
import argparse
import os
import shutil
import signal
import sys
import tempfile
import threading
from datetime import datetime

if sys.platform == "win32":
    # Emoji in the log output need a UTF-8 console; older Windows terminals
    # default to cp1252 and would raise on the first emoji.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

from . import n64_core as core
from . import patchdb
from .batch_runner import batch_patch_roms
from .header_utils import detect_and_strip_scene_header, fix_rom_crc
from .ips_bps_patcher import apply_bps_patch, apply_ips_patch, detect_patch_type
from .presets import PRESETS, apply_preset, list_presets
from .zip_handler import (
    cleanup_temp_dir,
    create_extraction_dir,
    extract_roms_from_archive,
    is_archive,
)


class RunLogger:
    """Prints to stdout and collects lines for the persistent log file."""

    def __init__(self):
        self.lines = []

    def __call__(self, msg=""):
        print(msg)
        self.lines.append(str(msg))

    def flush_to_file(self):
        if self.lines:
            try:
                core.append_log(self.lines)
            except OSError:
                pass


def collect_roms_and_archives(paths: list, recursive: bool = False) -> tuple:
    """
    Collect ROMs and archives separately.
    Returns: (rom_files, archive_files)
    """
    roms = []
    archives = []

    for path in paths:
        if os.path.isdir(path):
            walker = os.walk(path) if recursive else [(path, [], os.listdir(path))]
            for root, _, files in walker:
                for f in files:
                    full_path = os.path.join(root, f)
                    if is_archive(full_path):
                        archives.append(full_path)
                    elif core.is_rom_file(f) and not core.is_tool_output(full_path):
                        roms.append(full_path)
        elif is_archive(path):
            archives.append(path)
        elif core.is_rom_file(path) and not core.is_tool_output(path):
            roms.append(path)

    return roms, archives


def _clean_base_name(rom_path: str) -> str:
    """Filename stem without any tag this tool may have added earlier."""
    base_fn, _ = os.path.splitext(os.path.basename(rom_path))
    for t in core.OUTPUT_TAGS:
        if base_fn.endswith(t):
            return base_fn[:-len(t)]
    return base_fn


def _tagged_output_path(rom_path: str, tag: str, output_dir=None) -> str:
    """Tagged output path that collides neither with the input nor with an
    existing file (see core._free_output_path)."""
    dir_name = output_dir or (os.path.dirname(os.path.abspath(rom_path)) or ".")
    suffix = f"{tag}.z64"
    base_fn = core._fit_base_name(_clean_base_name(rom_path), suffix)
    return core._free_output_path(os.path.join(dir_name, base_fn + suffix),
                                  avoid=rom_path)

def apply_community_patch(rom_path: str, patch_path: str, output_dir=None,
                          strip_header: bool = False, fix_crc: bool = False,
                          log=print) -> dict:
    """
    Apply an .ips/.bps community patch to the CLEAN ROM (community
    patches are built against unmodified dumps).
    """
    patch_type = detect_patch_type(patch_path)
    if patch_type == "ups":
        return {"status": "error", "message": "UPS format is not supported"}
    if patch_type not in ("ips", "bps"):
        return {"status": "error",
                "message": f"Unknown patch format: {patch_type}"}

    workdir = tempfile.mkdtemp(prefix="n64_community_")
    try:
        base_rom = rom_path
        if strip_header:
            stripped_tmp = os.path.join(workdir, "stripped.z64")
            header_res = detect_and_strip_scene_header(rom_path, stripped_tmp)
            if header_res.get("stripped"):
                base_rom = stripped_tmp
                log(f"   🔧 {header_res['message']}")

        clean_z64 = os.path.join(workdir, "clean.z64")
        if not core.ensure_z64(base_rom, clean_z64):
            return {"status": "error",
                    "message": "Not a recognizable N64 ROM (bad header magic)"}

        patched = os.path.join(workdir, "patched.z64")
        if patch_type == "ips":
            res = apply_ips_patch(clean_z64, patch_path, patched)
        else:
            res = apply_bps_patch(clean_z64, patch_path, patched)
        if res.get("status") != "patched":
            return {"status": "error", "message": res.get("message", "Patch failed")}

        if fix_crc:
            crc_res = fix_rom_crc(patched)
            log(f"   🔧 {crc_res.get('message')}")

        final_path = _tagged_output_path(rom_path, " [COMMUNITY]", output_dir)
        os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
        shutil.move(patched, final_path)
        return {"status": "patched", "output": final_path,
                "message": res.get("message", "")}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def make_crcfix_copy(rom_path: str, output_dir=None, strip_header: bool = False,
                     log=print) -> dict:
    """Create a CRC-repaired .z64 copy of a (skipped) ROM."""
    workdir = tempfile.mkdtemp(prefix="n64_crcfix_")
    try:
        base_rom = rom_path
        if strip_header:
            stripped_tmp = os.path.join(workdir, "stripped.z64")
            header_res = detect_and_strip_scene_header(rom_path, stripped_tmp)
            if header_res.get("stripped"):
                base_rom = stripped_tmp
                log(f"   🔧 {header_res['message']}")

        clean_z64 = os.path.join(workdir, "clean.z64")
        if not core.ensure_z64(base_rom, clean_z64):
            return {"status": "error", "message": "Not a recognizable N64 ROM"}

        crc_res = fix_rom_crc(clean_z64)
        if crc_res.get("status") != "fixed":
            return {"status": "error", "message": crc_res.get("message", "CRC fix failed")}

        final_path = _tagged_output_path(rom_path, " [CRCFIX]", output_dir)
        os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
        shutil.move(clean_z64, final_path)
        return {"status": "fixed", "output": final_path,
                "message": crc_res.get("message", "")}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="n64patcher",
        description="Universal N64 ROM Inspector & Smart Patcher - headless mode. "
                    "Original ROMs are never modified."
    )

    # Input/Output
    parser.add_argument("inputs", nargs="*", help="ROM files, archives (.zip/.7z) or folders")
    parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into subfolders")
    parser.add_argument("-o", "--output-dir", help="Output directory (default: next to the ROMs)")

    # Presets
    parser.add_argument("--preset", choices=list(PRESETS.keys()),
                        help="Use a preconfigured preset profile")

    # Individual options (override the preset when given)
    parser.add_argument("--hires", action="store_true",
                        help="640x480 hi-res patching (only applied to dumps with "
                             "a verified patch; others are reported and skipped)")
    parser.add_argument("--force-hires", action="store_true",
                        help="Apply the generic VI-table widening even without a "
                             "verified patch. Renders incorrectly on hardware: "
                             "doubled image, misplaced UI. Experimental.")
    parser.add_argument("--keep-aa", action="store_true", help="KEEP anti-aliasing")
    parser.add_argument("--no-dither", action="store_true", help="REMOVE dithering")
    parser.add_argument("--no-divot", action="store_true", help="REMOVE the divot filter")
    parser.add_argument("--no-gamma", action="store_true", help="REMOVE the gamma boost")

    # Community Patches
    parser.add_argument("--patch-file", help=".ips or .bps file to apply to ALL ROMs "
                                             "(replaces the engine pipeline; applied to clean ROMs)")

    # Performance
    parser.add_argument("-j", "--jobs", type=int, default=4, help="Parallel workers (default: 4)")

    # Flashcart-Optionen
    parser.add_argument("--strip-header", action="store_true",
                        help="Strip scene intro headers (for xdelta compatibility)")
    parser.add_argument("--fix-crc", action="store_true",
                        help="Repair CRC1/CRC2 for flashcarts (creates [CRCFIX] copies "
                             "for skipped ROMs; patched outputs are always corrected)")

    # Inspection
    parser.add_argument("--inspect-only", action="store_true", help="Inspect only, do not patch")
    parser.add_argument("--export", help="Export a report as CSV or JSON")
    parser.add_argument("--list-presets", action="store_true", help="List the available presets")
    parser.add_argument("--list-patches", action="store_true",
                        help="List the patch recipes and the directories they load from")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing any files")
    parser.add_argument("--verify", action="store_true",
                        help="Independently re-check every output after patching "
                             "(format, CIC, boot checksums, expected VI state). "
                             "Exit code 1 on failure.")
    parser.add_argument("--verify-report",
                        help="Write the verification matrix as CSV or JSON "
                             "(hashes + result, no ROM data)")
    parser.add_argument("--version", action="store_true", help="Show the version")

    args = parser.parse_args(argv)
    log = RunLogger()

    if args.version:
        log(f"n64patcher v{core.VERSION}")
        return 0

    if args.list_patches:
        log(patchdb.describe(core.PATCH_DB))
        problems = core.patch_db_problems()
        for problem in problems:
            log(f"⚠️  {problem}")
        # Non-zero so a malformed recipe fails a CI check rather than
        # being noticed only when a ROM quietly misses its patch.
        return 1 if problems else 0

    if args.list_presets:
        log("\n🎮 Available presets:\n")
        for preset in list_presets():
            # The key is what --preset actually takes, so lead with it;
            # listing only the display name left no way to know what to type.
            log(f"  {preset['key']}  -  {preset['name']}")
            log(f"    {preset['description']}\n")
        log("Use with: n64patcher <inputs> --preset <key>\n")
        return 0

    if not args.inputs:
        parser.print_help()
        return 1

    if args.output_dir:
        args.output_dir = os.path.abspath(args.output_dir)
        os.makedirs(args.output_dir, exist_ok=True)

    # Check tool availability
    tools = core.check_tools()
    missing = [name for name in ("u64aap", "rn64crc", "xdelta3") if not tools.get(name)]
    if missing:
        log(f"⚠️  WARNING: tools not runnable: {', '.join(missing)}")
        log("   (affected stages fall back: No-AA via the dynamic patcher, "
            "CRC via the pure-Python engine)\n")

    # Collect input files
    log("🔍 Scanning inputs...")
    roms, archives = collect_roms_and_archives(args.inputs, args.recursive)

    temp_dirs = []
    rom_output_dir = {}  # rom path -> desired output dir (None = neben der Quelle)
    exit_code = 0
    try:
        # Loose ROMs: output next to the source (or -o)
        for rom in roms:
            rom_output_dir[rom] = args.output_dir

        # Archive extrahieren
        for archive in archives:
            log(f"📦 Extracting: {os.path.basename(archive)}")
            temp_dir = create_extraction_dir()
            try:
                extracted = extract_roms_from_archive(archive, temp_dir)
                roms.extend(extracted)
                temp_dirs.append(temp_dir)
                # Extracted ROMs: output next to the archive (or -o),
                # NOT into the temp dir - that gets cleaned up.
                archive_dir = os.path.dirname(os.path.abspath(archive)) or "."
                for ex in extracted:
                    rom_output_dir[ex] = args.output_dir or archive_dir
                log(f"   ✓ {len(extracted)} ROM(s) extracted")
            except RuntimeError as e:
                cleanup_temp_dir(temp_dir)
                log(f"   ❌ {e}")

        if not roms:
            log("\n❌ No ROM files found.")
            return 1

        log(f"\n🎮 {len(roms)} ROM(s) found.\n")

        # Dry run: print the plan and stop
        if args.dry_run:
            log("🧪 DRY RUN - no files will be written.")
            log(f"   ROMs:       {len(roms)}")
            log(f"   Preset:     {args.preset or 'none (individual flags)'}")
            log(f"   Options:    hires={args.hires or (args.preset and PRESETS[args.preset].options.hires)} "
                f"no_dither={args.no_dither} no_divot={args.no_divot} no_gamma={args.no_gamma} "
                f"keep_aa={args.keep_aa}")
            log(f"   Patch file: {args.patch_file or 'none'}")
            log(f"   Output dir: {args.output_dir or 'next to the ROMs'}")
            log(f"   Workers:    {args.jobs}")
            for rom in roms:
                log(f"   - {rom}")
            return 0

        # Inspection mode
        if args.inspect_only or args.export:
            infos = []
            for rom in roms:
                try:
                    info = core.inspect_rom_details(rom, with_hashes=True)
                except Exception as e:
                    log(f"⚠️  Error inspecting {os.path.basename(rom)}: {e}")
                    continue
                infos.append(info)
                res = "640x480" if info["is_hires_640x480"] else "320x240"
                aa = "No-AA" if info["no_aa"] else "AA"
                hires_label = {
                    core.HIRES_VERIFIED: "hi-res: verified",
                    core.HIRES_NATIVE: "hi-res: native",
                    core.HIRES_UNSUPPORTED: "hi-res: unsupported",
                }.get(info.get("hires_support"), "hi-res: ?")
                log(f"{info['filename']}: {info['title']} [{info['region']}] "
                    f"{info['format']} | {res} | {aa} | VI: {info['vi_table_count']} "
                    f"| {hires_label}")

            if args.export:
                core.export_report(infos, args.export)
                log(f"\n✅ Report written: {args.export}")
            return 0

        # Community patch mode (applied to CLEAN ROMs, replaces the engine)
        if args.patch_file:
            if not os.path.isfile(args.patch_file):
                log(f"❌ Patch file not found: {args.patch_file}")
                return 1
            log(f"🎨 Applying community patch: {os.path.basename(args.patch_file)}")
            log("   (on clean ROMs; the engine pipeline is skipped)\n")
            patched = errors = 0
            for rom in roms:
                res = apply_community_patch(
                    rom, args.patch_file, output_dir=rom_output_dir.get(rom),
                    strip_header=args.strip_header, fix_crc=args.fix_crc, log=log)
                if res["status"] == "patched":
                    patched += 1
                    log(f"   ✓ {os.path.basename(rom)} -> {os.path.basename(res['output'])} "
                        f"({res['message']})")
                else:
                    errors += 1
                    log(f"   ❌ {os.path.basename(rom)}: {res['message']}")
            log(f"\n{'='*60}")
            log(f"✅ Done. Community-patched: {patched}, errors: {errors}")
            log(f"{'='*60}\n")
            return 1 if errors > 0 else 0

        # Assemble patch options (individual flags override presets)
        if args.preset:
            log(f"📋 Preset: {PRESETS[args.preset].name}")
            options = apply_preset(args.preset)
            if args.keep_aa:
                options.no_aa = False
            if args.no_dither:
                options.no_dither = True
            if args.no_divot:
                options.no_divot = True
            if args.no_gamma:
                options.no_gamma = True
            if args.hires:
                options.hires = True
        else:
            options = core.PatchOptions(
                no_aa=not args.keep_aa,
                no_dither=args.no_dither,
                no_divot=args.no_divot,
                no_gamma=args.no_gamma,
                hires=args.hires,
            )
        options.force_hires = args.force_hires
        if args.force_hires:
            options.hires = True
            log("⚠️  --force-hires: applying the generic VI-table widening to "
                "unverified dumps.\n"
                "   This renders incorrectly on hardware (doubled image, "
                "misplaced UI).\n")

        # Header-Stripping (vor dem Patchen)
        stripped_tmp_files = []
        if args.strip_header:
            log("🔧 Stripping scene headers...")
            stripped_roms = []
            for rom in roms:
                temp_stripped = rom + ".stripped.z64"
                result = detect_and_strip_scene_header(rom, temp_stripped)
                if result["stripped"]:
                    log(f"   ✓ {os.path.basename(rom)}: {result['message']}")
                    stripped_roms.append(temp_stripped)
                    stripped_tmp_files.append(temp_stripped)
                    if rom in rom_output_dir:
                        rom_output_dir[temp_stripped] = rom_output_dir[rom]
                else:
                    stripped_roms.append(rom)
            roms = stripped_roms
            log("")

        # Multi-threaded batch patching (grouped by target directory,
        # so ROMs extracted from archives don't land in the temp dir)
        log(f"🚀 Starting batch patching ({args.jobs} worker(s))...\n")
        groups = {}
        for rom in roms:
            groups.setdefault(rom_output_dir.get(rom), []).append(rom)

        results = {"patched": 0, "skipped": 0, "errors": 0, "results": []}
        # An Event rather than a plain flag: it is read from worker threads,
        # and SIGINT sets it from the signal handler while a group is still
        # running, which a closed-over local could not express.
        cancel = threading.Event()
        previous_sigint = signal.getsignal(signal.SIGINT)

        def _on_sigint(_signum, _frame):
            cancel.set()
            log("\n⛔ Cancellation requested - finishing in-flight ROMs...")

        try:
            signal.signal(signal.SIGINT, _on_sigint)
        except ValueError:
            previous_sigint = None  # not on the main thread; keep default

        try:
            for group_dir, group_roms in groups.items():
                summary = batch_patch_roms(group_roms, options, max_workers=args.jobs,
                                           log_func=lambda m: log(f"   {m}"),
                                           output_dir=group_dir,
                                           should_cancel=cancel.is_set)
                results["patched"] += summary["patched"]
                results["skipped"] += summary["skipped"]
                results["errors"] += summary["errors"]
                results["results"].extend(summary["results"])
                if summary.get("cancelled"):
                    # Ctrl+C during one group also stops the remaining groups.
                    cancel.set()
                    log("\n⛔ Cancelled - already-finished ROMs are kept.")
                    break
        finally:
            if previous_sigint is not None:
                signal.signal(signal.SIGINT, previous_sigint)

        for res in results["results"]:
            name = os.path.basename(res.get("input", ""))
            if res["status"] == "patched":
                log(f"✅ {name} -> {os.path.basename(res['output'])}")
            elif res["status"] == "skipped":
                log(f"⏭️  {name}: {res.get('message', 'Skipped')}")
            else:
                log(f"❌ {name}: {res.get('message', 'Error')}")

        # Flashcart CRC repair: [CRCFIX] copies for skipped ROMs
        if args.fix_crc:
            skipped = [res for res in results["results"]
                       if res.get("status") in ("skipped", "error")]
            if skipped:
                log(f"\n🔧 Creating CRC-repaired copies for {len(skipped)} "
                    f"skipped ROM(s)...")
                for res in skipped:
                    src = res.get("input")
                    if not src or not os.path.isfile(src):
                        continue
                    crc_res = make_crcfix_copy(src, output_dir=rom_output_dir.get(src),
                                               log=log)
                    if crc_res.get("status") == "fixed":
                        log(f"   ✓ {os.path.basename(src)} -> "
                            f"{os.path.basename(crc_res['output'])}")
                    else:
                        log(f"   ⚠️  {os.path.basename(src)}: {crc_res.get('message')}")

        # Verification: independently re-check every output
        verify_failed = 0
        if args.verify:
            patched = [r for r in results["results"] if r.get("output")]
            log(f"\n🔎 Verifying {len(patched)} output(s)...")
            for res in patched:
                verdict = core.verify_output(res["output"], res.get("applied"))
                name = os.path.basename(res["output"])
                if verdict["ok"]:
                    log(f"   ✓ {name}")
                else:
                    verify_failed += 1
                    log(f"   ✗ {name}")
                for check in verdict["checks"]:
                    if not check["ok"]:
                        mark = "✗" if check["strict"] else "·"
                        log(f"      {mark} {check['name']}: {check['detail']}")
            if args.verify_report:
                rows = core.verify_report_rows(results["results"])
                core.export_rows(rows, args.verify_report)
                log(f"   📄 Matrix written: {args.verify_report}")

        # Summary
        log(f"\n{'='*60}")
        log(f"✅ Done. Patched: {results['patched']}, Skipped: {results['skipped']}, "
            f"Errors: {results['errors']}")
        if args.verify:
            log(f"🔎 Verified: {results['patched'] - verify_failed} OK, "
                f"{verify_failed} failed")
        log(f"{'='*60}\n")
        exit_code = 1 if (results["errors"] > 0 or verify_failed > 0) else 0

        # Clean up stripped ROMs
        for tmp in stripped_tmp_files:
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return exit_code
    finally:
        # Clean up temporary extraction dirs (even on failure)
        for temp_dir in temp_dirs:
            cleanup_temp_dir(temp_dir)
        log(f"🕓 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - run finished")
        log.flush_to_file()


if __name__ == "__main__":
    sys.exit(main())
