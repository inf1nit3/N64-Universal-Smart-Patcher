# Changelog

## v2.1

**Architecture**
- Patch engine extracted into `n64_core.py` (pure standard library), shared by the GUI, the new headless CLI (`n64_patcher_cli.py`), and a 35-test unit suite (`test_n64_core.py`, synthetic ROMs - no game files needed).
- Reproducible release builds via `build_release.ps1` (GUI + CLI executables) and `requirements.txt`.

**Fixed**
- SubDrag `.xdelta` patches are now applied to the **clean** ROM first (they previously ran on already-u64aap-modified data and almost always failed, silently falling back to the generic engine).
- The "Disable Anti-Aliasing" checkbox is now honored - u64aap no longer runs unconditionally.
- Gamma boost removal is no longer hardcoded on; it is an explicit "Disable Gamma Boost" option (default off).
- `apply_dynamic_vi_patch` honors its AA/dither flags independently.
- Removed a hardcoded developer-machine backup path (`D:\BACKUP SUMMERCART64 INKL ROMS`).
- Unknown/non-ROM files are rejected by header magic instead of being patched anyway; odd-length overdumps no longer crash endianness conversion.
- All external tool calls have timeouts and tolerate missing executables (stages degrade with warnings instead of aborting the batch).
- "Already patched" heuristics scan only the code region (first 8 MiB), avoiding false positives from asset data.
- Logs append to `%APPDATA%\N64SmartPatcher\N64_Patcher_Log.txt` (per-user; works for installed bundles) instead of overwriting a file next to the EXE.
- Closing the window mid-run no longer crashes (QThread shutdown), and cancel support stops batch runs cleanly.
- Temp files use `tempfile`, are always cleaned up, and no longer collide between concurrent instances.
- Folder drops skip the tool's own tagged outputs and temp files; output paths can never collide with the input file.
- Extended region/country-code map (Brazil, Korea, PAL variants, etc.).

**Added**
- Headless CLI: `n64_patcher_cli.py` with recursive folder batching, `--hires`, `--keep-aa`, `--no-dither/--no-divot/--no-gamma`, `--inspect-only`, and `--export` (CSV/JSON).
- GUI: background inspection thread, Cancel button, MD5/SHA-1 hashes in the inspector, CSV/JSON report export, and persistent options (QSettings).

## v2.0
- Initial release: Smart VI Mode Table Engine, u64aap/rn64crc/xdelta3 integration, SubDrag hi-res patches, PyQt6 GUI.
