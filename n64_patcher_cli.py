"""
n64_patcher_cli.py
Universal N64 ROM Inspector & Smart Patcher v3.1 - Headless CLI

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
import sys
import tempfile
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import n64_core as core
from batch_runner import batch_patch_roms
from zip_handler import (is_archive, extract_roms_from_archive,
                         cleanup_temp_dir, create_extraction_dir)
from ips_bps_patcher import apply_ips_patch, apply_bps_patch, detect_patch_type
from presets import apply_preset, list_presets, PRESETS
from header_utils import detect_and_strip_scene_header, fix_rom_crc


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
    Sammelt ROMs UND Archive getrennt.
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
    dir_name = output_dir or (os.path.dirname(os.path.abspath(rom_path)) or ".")
    base_fn = _clean_base_name(rom_path)
    max_base_len = 65 - len(tag)
    if len(base_fn) > max_base_len:
        base_fn = base_fn[:max_base_len].rstrip(" _-")
    return os.path.join(dir_name, f"{base_fn}{tag}.z64")

def apply_community_patch(rom_path: str, patch_path: str, output_dir=None,
                          strip_header: bool = False, fix_crc: bool = False,
                          log=print) -> dict:
    """
    Wendet einen .ips/.bps Community-Patch auf das SAUBERE ROM an
    (Community-Patches werden gegen unveränderte Dumps gebaut).
    """
    patch_type = detect_patch_type(patch_path)
    if patch_type == "ups":
        return {"status": "error", "message": "UPS-Format wird nicht unterstützt"}
    if patch_type not in ("ips", "bps"):
        return {"status": "error",
                "message": f"Unbekanntes Patch-Format: {patch_type}"}

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
                    "message": "Kein erkennbares N64-ROM (Header-Magic fehlerhaft)"}

        patched = os.path.join(workdir, "patched.z64")
        if patch_type == "ips":
            res = apply_ips_patch(clean_z64, patch_path, patched)
        else:
            res = apply_bps_patch(clean_z64, patch_path, patched)
        if res.get("status") != "patched":
            return {"status": "error", "message": res.get("message", "Patch fehlgeschlagen")}

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
    """Erzeugt eine CRC-reparierte z64-Kopie eines (übersprungenen) ROMs."""
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
            return {"status": "error", "message": "Kein erkennbares N64-ROM"}

        crc_res = fix_rom_crc(clean_z64)
        if crc_res.get("status") != "fixed":
            return {"status": "error", "message": crc_res.get("message", "CRC-Fix fehlgeschlagen")}

        final_path = _tagged_output_path(rom_path, " [CRCFIX]", output_dir)
        os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
        shutil.move(clean_z64, final_path)
        return {"status": "fixed", "output": final_path,
                "message": crc_res.get("message", "")}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="n64_patcher_cli",
        description="Universal N64 ROM Inspector & Smart Patcher v3.1 - Headless Mode. "
                    "Original ROMs werden nie modifiziert."
    )

    # Input/Output
    parser.add_argument("inputs", nargs="*", help="ROM-Dateien, Archive (.zip/.7z) oder Ordner")
    parser.add_argument("-r", "--recursive", action="store_true", help="Rekursiv in Unterordner")
    parser.add_argument("-o", "--output-dir", help="Ausgabeverzeichnis (Standard: neben den ROMs)")

    # Presets
    parser.add_argument("--preset", choices=list(PRESETS.keys()),
                        help="Vorkonfiguriertes Preset verwenden")

    # Individuelle Optionen (überschreiben das Preset, wenn angegeben)
    parser.add_argument("--hires", action="store_true", help="640x480 High-Res Patching")
    parser.add_argument("--keep-aa", action="store_true", help="Anti-Aliasing BEIBEHALTEN")
    parser.add_argument("--no-dither", action="store_true", help="Dithering ENTFERNEN")
    parser.add_argument("--no-divot", action="store_true", help="Divot-Filter ENTFERNEN")
    parser.add_argument("--no-gamma", action="store_true", help="Gamma-Boost ENTFERNEN")

    # Community Patches
    parser.add_argument("--patch-file", help=".ips oder .bps Datei für ALLE ROMs anwenden "
                                             "(ersetzt die Engine-Pipeline; wirkt auf saubere ROMs)")

    # Performance
    parser.add_argument("-j", "--jobs", type=int, default=4, help="Parallele Worker (Standard: 4)")

    # Flashcart-Optionen
    parser.add_argument("--strip-header", action="store_true",
                        help="Scene-Intro-Header entfernen (für xdelta-Kompatibilität)")
    parser.add_argument("--fix-crc", action="store_true",
                        help="CRC1/CRC2 für Flashcarts reparieren (erzeugt [CRCFIX]-Kopien "
                             "für übersprungene ROMs; gepatchte Ausgaben werden immer korrigiert)")

    # Inspection
    parser.add_argument("--inspect-only", action="store_true", help="Nur inspizieren, nicht patchen")
    parser.add_argument("--export", help="Report als CSV oder JSON exportieren")
    parser.add_argument("--list-presets", action="store_true", help="Verfügbare Presets anzeigen")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur anzeigen was getan würde, keine Dateien schreiben")
    parser.add_argument("--version", action="store_true", help="Version anzeigen")

    args = parser.parse_args(argv)
    log = RunLogger()

    if args.version:
        log(f"n64_patcher_cli v{core.VERSION}")
        return 0

    if args.list_presets:
        log("\n🎮 Verfügbare Presets:\n")
        for preset in list_presets():
            log(f"  {preset['name']}")
            log(f"    {preset['description']}\n")
        return 0

    if not args.inputs:
        parser.print_help()
        return 1

    if args.output_dir:
        args.output_dir = os.path.abspath(args.output_dir)
        os.makedirs(args.output_dir, exist_ok=True)

    # Tools prüfen
    tools = core.check_tools()
    missing = [name for name in ("u64aap", "rn64crc", "xdelta3") if not tools.get(name)]
    if missing:
        log(f"⚠️  WARNING: Nicht ausführbare Tools: {', '.join(missing)}")
        log("   (Betroffene Stufen nutzen Fallbacks: No-AA via Dynamic-Patcher, "
            "CRC via Pure-Python-Engine)\n")

    # Dateien sammeln
    log("🔍 Scanne Eingaben...")
    roms, archives = collect_roms_and_archives(args.inputs, args.recursive)

    temp_dirs = []
    rom_output_dir = {}  # rom path -> desired output dir (None = neben der Quelle)
    exit_code = 0
    try:
        # Lose ROMs: Ausgabe neben der Quelle (oder -o)
        for rom in roms:
            rom_output_dir[rom] = args.output_dir

        # Archive extrahieren
        for archive in archives:
            log(f"📦 Extrahiere: {os.path.basename(archive)}")
            temp_dir = create_extraction_dir()
            try:
                extracted = extract_roms_from_archive(archive, temp_dir)
                roms.extend(extracted)
                temp_dirs.append(temp_dir)
                # Extrahierte ROMs: Ausgabe neben dem Archiv (oder -o),
                # NICHT in das Temp-Verzeichnis (das wird aufgeräumt).
                archive_dir = os.path.dirname(os.path.abspath(archive)) or "."
                for ex in extracted:
                    rom_output_dir[ex] = args.output_dir or archive_dir
                log(f"   ✓ {len(extracted)} ROM(s) extrahiert")
            except RuntimeError as e:
                cleanup_temp_dir(temp_dir)
                log(f"   ❌ {e}")

        if not roms:
            log("\n❌ Keine ROM-Dateien gefunden.")
            return 1

        log(f"\n🎮 {len(roms)} ROM(s) gefunden.\n")

        # Dry-Run: Plan anzeigen und beenden
        if args.dry_run:
            log("🧪 DRY RUN - es werden keine Dateien geschrieben.")
            log(f"   ROMs:      {len(roms)}")
            log(f"   Preset:    {args.preset or 'keins (Einzelflaggen)'}")
            log(f"   Optionen:  hires={args.hires or (args.preset and PRESETS[args.preset].options.hires)} "
                f"no_dither={args.no_dither} no_divot={args.no_divot} no_gamma={args.no_gamma} "
                f"keep_aa={args.keep_aa}")
            log(f"   Patch-Datei: {args.patch_file or 'keine'}")
            log(f"   Output-Dir:  {args.output_dir or 'neben den ROMs'}")
            log(f"   Worker:      {args.jobs}")
            for rom in roms:
                log(f"   - {rom}")
            return 0

        # Inspection-Modus
        if args.inspect_only or args.export:
            infos = []
            for rom in roms:
                try:
                    info = core.inspect_rom_details(rom, with_hashes=True)
                except Exception as e:
                    log(f"⚠️  Fehler bei Inspektion von {os.path.basename(rom)}: {e}")
                    continue
                infos.append(info)
                res = "640x480" if info["is_hires_640x480"] else "320x240"
                aa = "No-AA" if info["no_aa"] else "AA"
                log(f"{info['filename']}: {info['title']} [{info['region']}] "
                    f"{info['format']} | {res} | {aa} | VI: {info['vi_table_count']}")

            if args.export:
                core.export_report(infos, args.export)
                log(f"\n✅ Report geschrieben: {args.export}")
            return 0

        # Community-Patch-Modus (wirkt auf SAUBERE ROMs, ersetzt die Engine)
        if args.patch_file:
            if not os.path.isfile(args.patch_file):
                log(f"❌ Patch-Datei nicht gefunden: {args.patch_file}")
                return 1
            log(f"🎨 Wende Community-Patch an: {os.path.basename(args.patch_file)}")
            log("   (auf sauberen ROMs; Engine-Pipeline wird übersprungen)\n")
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
            log(f"✅ Fertig! Community-Patched: {patched}, Errors: {errors}")
            log(f"{'='*60}\n")
            return 1 if errors > 0 else 0

        # Patch-Optionen zusammenstellen (Einzelflaggen überschreiben Presets)
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

        # Header-Stripping (vor dem Patchen)
        stripped_tmp_files = []
        if args.strip_header:
            log("🔧 Entferne Scene-Header...")
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

        # Batch-Patching mit Multithreading (gruppiert nach Zielverzeichnis,
        # damit extrahierte Archiv-ROMs nicht im Temp-Verzeichnis landen)
        log(f"🚀 Starte Batch-Patching ({args.jobs} Worker)...\n")
        groups = {}
        for rom in roms:
            groups.setdefault(rom_output_dir.get(rom), []).append(rom)

        results = {"patched": 0, "skipped": 0, "errors": 0, "results": []}
        for group_dir, group_roms in groups.items():
            summary = batch_patch_roms(group_roms, options, max_workers=args.jobs,
                                       log_func=lambda m: log(f"   {m}"),
                                       output_dir=group_dir)
            results["patched"] += summary["patched"]
            results["skipped"] += summary["skipped"]
            results["errors"] += summary["errors"]
            results["results"].extend(summary["results"])

        for res in results["results"]:
            name = os.path.basename(res.get("input", ""))
            if res["status"] == "patched":
                log(f"✅ {name} -> {os.path.basename(res['output'])}")
            elif res["status"] == "skipped":
                log(f"⏭️  {name}: {res.get('message', 'Skipped')}")
            else:
                log(f"❌ {name}: {res.get('message', 'Error')}")

        # CRC-Fixing für Flashcarts: [CRCFIX]-Kopien für übersprungene ROMs
        if args.fix_crc:
            skipped = [res for res in results["results"]
                       if res.get("status") in ("skipped", "error")]
            if skipped:
                log(f"\n🔧 Erzeuge CRC-reparierte Kopien für {len(skipped)} "
                    f"übersprungene ROM(s)...")
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

        # Zusammenfassung
        log(f"\n{'='*60}")
        log(f"✅ Fertig! Patched: {results['patched']}, Skipped: {results['skipped']}, "
            f"Errors: {results['errors']}")
        log(f"{'='*60}\n")
        exit_code = 1 if results["errors"] > 0 else 0

        # Cleanup gestrippte ROMs
        for tmp in stripped_tmp_files:
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return exit_code
    finally:
        # Cleanup temporäre Extraktionsverzeichnisse (auch bei Fehlern)
        for temp_dir in temp_dirs:
            cleanup_temp_dir(temp_dir)
        log(f"🕓 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Lauf beendet")
        log.flush_to_file()


if __name__ == "__main__":
    sys.exit(main())
