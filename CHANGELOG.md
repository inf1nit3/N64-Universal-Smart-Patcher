# Changelog

## v3.3.1 - Console Theme

- Console-era GUI theme in `theme.py`: faceplate strip over a four-segment accent rule, cartridge-label panels with coloured spines, bevelled controls with real press travel, and bitmap-era typography (Fixedsys/Terminal where present, graceful fallbacks elsewhere). Monospace is reserved for technical readouts; prose keeps the body face, which also stops preset descriptions forcing the window wider than its configured geometry.
- The run control is a round red START key in a recessed collar, centred between Inspect and Cancel. Generic industrial design - a red circle and the word START belong to nobody - and it stays a plain QPushButton with an accessible name, so automation and screen readers are unaffected.
- Front-panel indicator in the status bar tracks run state: idle, working, clean, errors.
- Superseded first theme pass: dark moulded-plastic panels, bevelled controls with real press travel, four primary accent colours used functionally, coloured tab underlines. Contains no Nintendo logo, wordmark, typeface or character art, and the module documents those limits inline.
- Fixed a state bug the theme exposed: a checkbox that was checked *and* disabled (which is what a locked preset produces) rendered as unchecked, so the UI misreported what a run would do. Checked-disabled now shows a dimmed fill.
- The preset warning label no longer reserves a blank strip when there is nothing to warn about.
- README gained a trademark section stating the project is unaffiliated with Nintendo and ships no ROMs.

## v3.3 - Hi-Res Gating

- Frozen builds now bundle `patches/*.json` and resolve it via `sys._MEIPASS`. Without this the shipped EXE loaded zero recipes and reported every verified dump as unsupported - caught by running the built binary rather than trusting the build.
- **BPS applier: SourceRead/TargetRead action numbers were swapped**, so no real-world `.bps` patch could be applied - each failed its target CRC32 check. Confirmed against two independent implementations of the spec. The test encoder shared the same swapped numbering, which is why the suite never caught it; it now imports the named constants from the module under test, and a new test builds a patch from the spec text inline.
- **BPS patch creation** via `--create-patch`. Verified on a real 12 MB ROM pair: a 65-byte patch reconstructs the target byte-for-byte.
- **Undo manifests** via `--manifest`, `--show-manifest` and `--revert`. Records every changed byte run plus both file hashes; revert verifies the restored file against the recorded SHA-1 and refuses if the patched file was altered. Large change sets (delta patches) are summarised and marked non-revertible rather than pretending.
- **No-Intro/Redump DAT lookup** via `--dat` or `~/.n64patcher/dats/`, reporting verified-dump status and the catalogued name. DAT files are deliberately not bundled. Parsed indexes are cached and invalidated on mtime/size change. Hashing stays lazy so a DAT-less scan costs nothing extra.
- **Patch recipes moved out of Python into a declarative database** (`patches/*.json`), user-extensible via `~/.n64patcher/patches/`. New `--list-patches`. Malformed entries are reported and skipped, not fatal. See `docs/PATCH_DB.md`.
- **640x480 is no longer offered for ROMs that cannot take it.** Widening an `OSViMode` entry changes one field; the framebuffer the game allocated and the RDP coordinates it draws with still assume 320. On hardware (verified on a SummerCart64) that produces a doubled image, menus rendered at the wrong size and UI in the wrong position. Hi-res now applies only where a verified per-dump patch exists; everything else is reported and skipped, with the reason. `--force-hires` applies it anyway and labels the result EXPERIMENTAL.
- Inspection reports `hires_support` (`verified` / `native` / `unsupported`) plus a reason, in the CLI listing, the Inspector table and the CSV/JSON export.
- The GUI's High-Res checkbox is disabled when no loaded ROM has a verified patch, with a tooltip explaining why; it names the count when some do.

## v3.2 - Production Hardening

- **SubDrag patches keyed on CRC1/CRC2 instead of the internal title.** Title matching never worked for Banjo-Kazooie (internal title `Banjo-Kazooie`, key `BANJO KAZOOIE`) or Forsaken 64 (internal title `Forsaken`, key `FORSAKEN 64`) - two of the eight advertised integrations were dead. Every checksum pair was derived empirically by applying the delta to candidate dumps and recording which succeeded; the Banjo delta turns out to target Rev A only, which region gating could not have expressed.
- CRC engine validated against a 1,549-file library: 1,492 of 1,496 stamped images reproduce their header checksums exactly (99.73%), covering CIC 6101/6102/6103/6105/6106.
- **Flashcart CRC repair actually repairs**: `header_utils.fix_rom_crc` (behind `--fix-crc`, `--patch-file --fix-crc` and the GUI flashcart checkbox) reported `CRC1/CRC2 repaired (rn64crc)` while the checksums stayed untouched, because rn64crc exits 0 on failure. It now re-reads the file and falls back to the native engine.
- **UI is fully English**: every GUI label, CLI help string, runtime message and the `header_utils` docstrings were German or mixed; all translated.
- `--list-presets` now prints the key `--preset` expects, not just the display name.
- **Packaging**: installable `n64patcher` package (src layout), console entry points `n64patcher` / `n64patcher-gui`, and a dependency split — the engine and CLI now install with **no third-party dependencies**; PyQt6 and py7zr are extras.
- **Data-loss fixes**: output filenames no longer collide (long names keep a digest instead of being truncated to a shared prefix; `(2)`, `(3)`... disambiguate; names are claimed atomically so parallel workers cannot race).
- **CRC engine**: corrected an off-by-equality in the mixing loop (`a2 == d` took the wrong branch, producing a wrong CRC2 with a correct CRC1); byte-swapped `.v64`/`.n64` images are now handled and keep their byte order; verified against an independent reference implementation across all 9 supported CIC variants.
- **External tools are no longer trusted on exit code alone**: `rn64crc` exits 0 even when it cannot identify the boot chip and leaves the header untouched — outputs are now checked and fall back to the native engine. Same for `u64aap`, which was detected by grepping stdout for an English phrase.
- **Dynamic VI patcher** is bounded to the code segment (`0x1000`–8 MB), requires word alignment, and aborts on implausible match density. It previously rewrote every 4-byte match in the whole ROM, including IPL3 bootcode — which breaks CIC identification outright.
- **Archive hardening**: the extraction cap is enforced on bytes as they arrive rather than on attacker-controlled declared sizes; compression-ratio limit; symlink members rejected; members are written to the validated path instead of letting the extractor choose.
- **Concurrency**: batch runs are cancellable (Ctrl+C included, keeping finished work), log output is drained on a single thread, and working files no longer land in a possibly read-only input directory.
- **New**: `--verify` / `--verify-report` re-check every output independently and emit a publishable pass/fail matrix (hashes + result, no ROM data).
- **Tooling**: `ruff` and `mypy` clean and enforced in CI; test suite grown to 148; CI additionally proves a dependency-free install and that the wheel actually contains the bundled helpers.
- Also fixed: IPS patches applied to byte-swapped ROMs without complaint; `no_aa` was OR'd with `no_dither`, so dither-patched ROMs were skipped as fully patched; `header_utils` reported a 3-character game code where the core reported 4; SubDrag deltas are region-gated instead of attempted on any title match.

## v3.1

**Critical fixes**
- BPS patcher rewritten against the reference spec (byuu/beat):
  variable-length values were decoded with `|=` instead of `+=` (corrupting
  multi-byte lengths), SourceCopy/TargetCopy negative offsets missed the
  `>> 1`, and commands 0/1 (TargetRead/SourceRead) were swapped.
  All three CRC32 footer checksums (source/target/patch) are now verified.
- `header_utils.get_rom_info_from_header` read the country byte from 0x3F
  and the version from 0x3E - they were swapped. Country is 0x3E
  (matching libdragon's REGION_OFFSET and the core engine).
- CLI `--output-dir` was parsed but silently ignored; it now works
  end-to-end (engine outputs, community patches, CRC-fix copies).
- CLI individual flags (`--keep-aa`, `--no-dither`, ...) now override the
  selected preset as documented.
- `--patch-file` applies community `.ips/.bps` patches to the CLEAN ROM
  (community patches are built against pristine dumps). Previously they
  were only applied to already-engine-patched outputs, silently skipping
  the common case.
- `presets.apply_preset` no longer hands out the shared mutable preset
  singleton; it returns a fresh `PatchOptions` each time.
- Scene-header stripping no longer full-copies ROMs that have no header;
  the GUI leaked of one `.stripped.z64` per clean ROM is gone and the
  suffix is recognized as tool output.
- `rn64crc` invocation normalized (`-u <file>`) in all call sites; a
  non-zero exit now triggers the fallback engine instead of silently
  producing stale checksums.
- File logging (per-user log file) is actually wired up now: CLI runs and
  GUI patch runs append to it (`append_log` was dead code before).
- Bundled `.exe` tools are only reported as available when they are
  actually executable (fixes PermissionError crashes on macOS/Linux);
  a system `xdelta3`/`xdelta` on PATH is used as fallback.

**Added**
- Pure-Python N64 boot CRC engine (CIC detection incl. 6101-6106, 64DD
  and Aleck64 variants; Project64-compatible algorithm). CRC1/CRC2 repair
  now works on macOS/Linux without rn64crc.exe; on Windows the bundled
  tool is still preferred.
- CLI: `--dry-run`, `--version`, `--list-presets`; `--fix-crc` additionally
  creates `[CRCFIX]` copies for skipped ROMs (flashcart repair mode);
  empty input shows usage; temp dirs are cleaned up even on errors
  (`try/finally`); runs are logged to the persistent log file.
- GUI: Inspector tab with sortable `QTreeWidget` table (title, region,
  CRC1/CRC2, hashes, ...), background inspection thread, CSV/JSON export,
  drag & drop of files/folders/archives, tool-availability status bar,
  current-file progress label, capped log view, persisted preset
  selection.
- mmap-based VI table scanner is now actually used: inspection of z64
  files no longer loads the whole ROM into memory; `mmap_vi_scanner`
  delegates to the shared core implementation.
- Archive extraction hardened: zip-slip protection (path traversal
  rejected for .zip and .7z), 1 GiB uncompressed size cap, unique system
  temp dirs instead of `_n64_temp_extract` next to the archive, errors
  surface as log messages instead of being swallowed. Outputs of ROMs
  extracted from archives are written next to the source archive (or
  `-o`), so they are no longer lost with the temp dir.
- IPS patcher: truncation-length support after the EOF marker, bounds
  checks on truncated patches.
- `PatchOptions.applied_tags` removed (unused mutable default field).
- Results carry the original `input` path; batch runner preserves input
  order and counts cancelled ROMs as skipped.
- Unix/macOS release build script `build_release.sh`; GitHub Actions CI
  (core suite on ubuntu/windows/macos x Python 3.11/3.12, GUI import
  check on Windows); runtime/build requirements split
  (`requirements.txt` / `requirements-dev.txt`), unused Pillow dropped.
- Test suite grown from 35 to 91 tests (new coverage: BPS reference
  vectors incl. multi-byte VLV & CRC verification, IPS RLE/truncation,
  header strip/CRC bytes, preset immutability, batch engine, zip-slip).

**GUI fixes**
- `PatchWorker` emits `done` instead of shadowing `QThread.finished`.
- Closing the window during a run cancels and waits for the worker and
  cleans up extraction temp dirs.

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
