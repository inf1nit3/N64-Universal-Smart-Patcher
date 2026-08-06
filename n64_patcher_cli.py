"""
n64_patcher_cli.py (ERWEITERT)
Universal N64 ROM Inspector & Smart Patcher v3.0 - Headless CLI
"""
import argparse
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import n64_core as core
from batch_runner import batch_patch_roms
from zip_handler import is_archive, extract_roms_from_archive, cleanup_temp_dir
from ips_bps_patcher import apply_ips_patch, apply_bps_patch, detect_patch_type
from presets import apply_preset, list_presets, PRESETS
from header_utils import detect_and_strip_scene_header, fix_rom_crc, get_rom_info_from_header


def collect_roms_and_archives(paths: list, recursive: bool = False) -> tuple:
    """
    Sammelt ROMs UND Archive getrennt.
    Returns: (rom_files, archive_files)
    """
    roms = []
    archives = []
    
    for path in paths:
        if os.path.isdir(path):
            if recursive:
                for root, _, files in os.walk(path):
                    for f in files:
                        full_path = os.path.join(root, f)
                        if is_archive(full_path):
                            archives.append(full_path)
                        elif core.is_rom_file(f):
                            if not core.is_tool_output(full_path):
                                roms.append(full_path)
            else:
                for f in os.listdir(path):
                    full_path = os.path.join(path, f)
                    if is_archive(full_path):
                        archives.append(full_path)
                    elif core.is_rom_file(f):
                        if not core.is_tool_output(full_path):
                            roms.append(full_path)
        else:
            if is_archive(path):
                archives.append(path)
            elif core.is_rom_file(path):
                if not core.is_tool_output(path):
                    roms.append(path)
    
    return roms, archives


def apply_community_patch(rom_path: str, patch_path: str, output_path: str) -> dict:
    """
    Wendet .ips, .bps oder .ups Patch an.
    """
    patch_type = detect_patch_type(patch_path)
    
    if patch_type == 'ips':
        return apply_ips_patch(rom_path, patch_path, output_path)
    elif patch_type == 'bps':
        return apply_bps_patch(rom_path, patch_path, output_path)
    elif patch_type == 'ups':
        return {"status": "error", "message": "UPS Support kommt in v3.1"}
    else:
        return {"status": "error", "message": f"Unbekanntes Patch-Format: {patch_type}"}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="n64_patcher_cli",
        description="Universal N64 ROM Inspector & Smart Patcher v3.0 - Headless Mode. "
                    "Original ROMs werden nie modifiziert."
    )
    
    # Input/Output
    parser.add_argument("inputs", nargs="*", help="ROM-Dateien, Archive (.zip/.7z) oder Ordner")
    parser.add_argument("-r", "--recursive", action="store_true", help="Rekursiv in Unterordner")
    parser.add_argument("-o", "--output-dir", help="Ausgabeverzeichnis (Standard: neben den ROMs)")
    
    # Presets
    parser.add_argument("--preset", choices=list(PRESETS.keys()),
                        help="Vorkonfiguriertes Preset verwenden")
    
    # Individuelle Optionen (überschreiben Preset wenn angegeben)
    parser.add_argument("--hires", action="store_true", help="640x480 High-Res Patching")
    parser.add_argument("--keep-aa", action="store_true", help="Anti-Aliasing BEIBEHALTEN")
    parser.add_argument("--no-dither", action="store_true", help="Dithering ENTFERNEN")
    parser.add_argument("--no-divot", action="store_true", help="Divot-Filter ENTFERNEN")
    parser.add_argument("--no-gamma", action="store_true", help="Gamma-Boost ENTFERNEN")
    
    # Community Patches
    parser.add_argument("--patch-file", help=".ips oder .bps Datei für ALLE ROMs anwenden")
    
    # Performance
    parser.add_argument("-j", "--jobs", type=int, default=4, help="Parallele Worker (Standard: 4)")
    
    # Flashcart-Optionen
    parser.add_argument("--strip-header", action="store_true", 
                        help="Scene-Intro-Header entfernen (für xdelta-Kompatibilität)")
    parser.add_argument("--fix-crc", action="store_true",
                        help="CRC1/CRC2 für Flashcarts reparieren")
    
    # Inspection
    parser.add_argument("--inspect-only", action="store_true", help="Nur inspizieren, nicht patchen")
    parser.add_argument("--export", help="Report als CSV oder JSON exportieren")
    parser.add_argument("--list-presets", action="store_true", help="Verfügbare Presets anzeigen")
    
    args = parser.parse_args(argv)
    
    # Presets anzeigen
    if args.list_presets:
        print("\n🎮 Verfügbare Presets:\n")
        for preset in list_presets():
            print(f"  {preset['name']}")
            print(f"    {preset['description']}\n")
        return 0
    
    # Tools prüfen
    tools = core.check_tools()
    missing = [name for name in ("u64aap", "rn64crc", "xdelta3") if not tools.get(name)]
    if missing:
        print(f"⚠️  WARNING: Fehlende Tools: {', '.join(missing)}")
        print(f"   (Betroffene Stufen werden übersprungen)\n")
    
    # Dateien sammeln
    print("🔍 Scanne Eingaben...")
    roms, archives = collect_roms_and_archives(args.inputs, args.recursive)
    
    # Archive extrahieren
    temp_dirs = []
    for archive in archives:
        print(f"📦 Extrahiere: {os.path.basename(archive)}")
        temp_dir = os.path.join(os.path.dirname(archive) or ".", "_n64_temp_extract")
        extracted = extract_roms_from_archive(archive, temp_dir)
        roms.extend(extracted)
        temp_dirs.append(temp_dir)
        print(f"   ✓ {len(extracted)} ROM(s) extrahiert")
    
    if not roms:
        print("\n❌ Keine ROM-Dateien gefunden.")
        return 1
    
    print(f"\n🎮 {len(roms)} ROM(s) gefunden.\n")
    
    # Inspection-Modus
    if args.inspect_only or args.export:
        infos = []
        for rom in roms:
            info = core.inspect_rom_details(rom, with_hashes=True)
            infos.append(info)
            res = "640x480" if info["is_hires_640x480"] else "320x240"
            aa = "No-AA" if info["no_aa"] else "AA"
            print(f"{info['filename']}: {info['title']} [{info['region']}] "
                  f"{info['format']} | {res} | {aa} | VI: {info['vi_table_count']}")
        
        if args.export:
            core.export_report(infos, args.export)
            print(f"\n✅ Report geschrieben: {args.export}")
        
        # Cleanup
        for temp_dir in temp_dirs:
            cleanup_temp_dir(temp_dir)
        return 0
    
    # Patch-Optionen zusammenstellen
    if args.preset:
        print(f"📋 Preset: {PRESETS[args.preset].name}")
        options = apply_preset(args.preset)
    else:
        options = core.PatchOptions(
            no_aa=not args.keep_aa,
            no_dither=args.no_dither,
            no_divot=args.no_divot,
            no_gamma=args.no_gamma,
            hires=args.hires,
        )
    
    # Header-Stripping (vor dem Patchen)
    if args.strip_header:
        print("🔧 Entferne Scene-Header...")
        stripped_roms = []
        for rom in roms:
            temp_stripped = rom + ".stripped.z64"
            result = detect_and_strip_scene_header(rom, temp_stripped)
            if result["stripped"]:
                print(f"   ✓ {os.path.basename(rom)}: {result['message']}")
                stripped_roms.append(temp_stripped)
            else:
                stripped_roms.append(rom)
        roms = stripped_roms
        print()
    
    # Community-Patch vorbereiten
    patch_file = args.patch_file
    if patch_file and not os.path.isfile(patch_file):
        print(f"⚠️  Patch-Datei nicht gefunden: {patch_file}")
        patch_file = None
    
    # Batch-Patching mit Multithreading
    print(f"🚀 Starte Batch-Patching ({args.jobs} Worker)...\n")
    results = batch_patch_roms(roms, options, max_workers=args.jobs, log_func=print)
    
    # Community-Patches anwenden (falls angegeben)
    if patch_file:
        print(f"\n🎨 Wende Community-Patch an: {os.path.basename(patch_file)}")
        for res in results["results"]:
            if res["status"] == "patched" and res["output"]:
                community_output = res["output"].replace(".z64", "_community.z64")
                patch_result = apply_community_patch(res["output"], patch_file, community_output)
                if patch_result["status"] == "patched":
                    print(f"   ✓ {os.path.basename(res['output'])} -> {patch_result['message']}")
    
    # CRC-Fixing für Flashcarts
    if args.fix_crc:
        print(f"\n🔧 Repariere CRC-Checksummen für Flashcarts...")
        rn64crc_path = core.RN64CRC_PATH
        for res in results["results"]:
            if res["status"] == "patched" and res["output"]:
                crc_result = fix_rom_crc(res["output"], rn64crc_path)
                print(f"   {crc_result['message']}: {os.path.basename(res['output'])}")
    
    # Zusammenfassung
    print(f"\n{'='*60}")
    print(f"✅ Fertig! Patched: {results['patched']}, Skipped: {results['skipped']}, Errors: {results['errors']}")
    print(f"{'='*60}\n")
    
    # Cleanup temporäre Verzeichnisse
    for temp_dir in temp_dirs:
        cleanup_temp_dir(temp_dir)
    
    # Cleanup gestrippte ROMs
    if args.strip_header:
        for rom in roms:
            if rom.endswith(".stripped.z64") and os.path.isfile(rom):
                try:
                    os.remove(rom)
                except:
                    pass
    
    return 1 if results["errors"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
