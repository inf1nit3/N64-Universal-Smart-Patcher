"""Headless command-line interface for the Universal N64 Smart Patcher.

Examples:
  python n64_patcher_cli.py rom1.z64 rom2.n64 --hires
  python n64_patcher_cli.py "D:\\N64 Roms" -r --hires --no-divot
  python n64_patcher_cli.py game.z64 --inspect-only
"""

import argparse
import os
import sys

import n64_core as core


def collect_roms(paths, recursive):
    files = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _, filenames in os.walk(p):
                    for fn in sorted(filenames):
                        full = os.path.join(root, fn)
                        if core.is_rom_file(full) and not core.is_tool_output(full):
                            files.append(full)
            else:
                for fn in sorted(os.listdir(p)):
                    full = os.path.join(p, fn)
                    if os.path.isfile(full) and core.is_rom_file(full) \
                            and not core.is_tool_output(full):
                        files.append(full)
        elif os.path.isfile(p):
            if core.is_rom_file(p):
                files.append(p)
            else:
                print(f"Skipping (not a ROM extension): {p}")
        else:
            print(f"Skipping (not found): {p}")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="n64_patcher_cli",
        description="Universal N64 ROM Inspector & Smart Patcher - headless mode. "
                    "Original ROMs are never modified; patched copies get a tagged filename.")
    parser.add_argument("inputs", nargs="+", help="ROM files and/or folders")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="Recurse into folders")
    parser.add_argument("--hires", action="store_true",
                        help="Enable 640x480 hi-res patching (SubDrag .xdelta preferred, "
                             "Smart VI Table engine as fallback)")
    parser.add_argument("--keep-aa", action="store_true",
                        help="Keep anti-aliasing (default: remove AA)")
    parser.add_argument("--no-dither", action="store_true",
                        help="Disable the dither filter")
    parser.add_argument("--no-divot", action="store_true",
                        help="Disable the divot filter (requires AA stage)")
    parser.add_argument("--no-gamma", action="store_true",
                        help="Disable gamma boost (requires AA stage)")
    parser.add_argument("--inspect-only", action="store_true",
                        help="Only inspect ROMs, do not patch")
    parser.add_argument("--export", metavar="REPORT",
                        help="Write inspection report to REPORT (.csv or .json)")
    args = parser.parse_args(argv)

    tools = core.check_tools()
    missing = [name for name in ("u64aap", "rn64crc", "xdelta3") if not tools[name]]
    if missing:
        print(f"WARNING: missing tools: {', '.join(missing)} "
              f"(affected stages will degrade or be skipped)")

    roms = collect_roms(args.inputs, args.recursive)
    if not roms:
        print("No ROM files found.")
        return 1

    print(f"{len(roms)} ROM(s) found.\n")

    if args.inspect_only or args.export:
        infos = []
        for rom in roms:
            info = core.inspect_rom_details(rom, with_hashes=True)
            infos.append(info)
            res = "640x480" if info["is_hires_640x480"] else "320x240"
            aa = "No-AA" if info["no_aa"] else "AA"
            print(f"{info['filename']}: {info['title']} [{info['region']}] "
                  f"{info['format']} | {res} | {aa} | VI tables: {info['vi_table_count']} | "
                  f"MD5 {info.get('md5', '-')}")
        if args.export:
            core.export_report(infos, args.export)
            print(f"\nReport written to {args.export}")
        if args.inspect_only:
            return 0

    options = core.PatchOptions(
        no_aa=not args.keep_aa,
        no_dither=args.no_dither,
        no_divot=args.no_divot,
        no_gamma=args.no_gamma,
        hires=args.hires,
    )

    patched = skipped = errors = 0
    for i, rom in enumerate(roms, 1):
        print(f"[{i}/{len(roms)}] {os.path.basename(rom)}")
        res = core.patch_rom(rom, options, log=lambda m: print(f"    {m}"))
        if res["status"] == "patched":
            patched += 1
            print(f"    -> CREATED {res['output']} ({', '.join(sorted(res['applied']))})")
        elif res["status"] == "skipped":
            skipped += 1
            print(f"    -- skipped: {res['message']}")
        else:
            errors += 1
            print(f"    XX error: {res['message']}")

    print(f"\nDone. Patched: {patched}, skipped: {skipped}, errors: {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
