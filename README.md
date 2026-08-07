# 🎮 Universal N64 ROM Inspector & Smart Patcher v3.2

![N64 Smart Patcher Icon](app_icon.png)

A modern, high-performance GUI + CLI ROM patcher and inspection utility for Nintendo 64 games. Features the **Smart VI Mode Table Engine v2.0** for structurally-verified 640x480 high-resolution patching, anti-aliasing (No-AA) removal, dither/divot/gamma filter toggles, SubDrag `.xdelta` integration, preset profiles, archive extraction, and Flashcart CRC/Header tools.

**Cross-platform**: the bundled Windows helpers (`u64aap.exe`, `rn64crc.exe`, `xdelta3.exe`) are used when runnable, with graceful fallbacks everywhere else — a **built-in pure-Python CRC1/CRC2 engine** (works on macOS/Linux), the dynamic VI instruction patcher for No-AA, and an optional system `xdelta3` from PATH.

Designed for use with real N64 hardware, FPGA consoles (Analogue 3D, ModRetro M64), flashcarts (SummerCart 64, EverDrive 64), and N64 emulators (Simple64, Ares, RMG).

---

## ✨ Key Features

- **🎯 Smart VI Mode Table Engine v2.0**:
  - Structural data pattern matching using 32-bit width words (`0x00000140`) paired with hardware NTSC/PAL/M-PAL burst-timing signatures (`0x03E52239`, `0x0404233A`, `0x04651E39`).
  - **Structurally constrained**: only width fields immediately followed by a
    known burst constant are modified, so unrelated data that merely contains
    `0x00000140` is left alone. See *Technical Details* for what this does and
    does not guarantee.
  - The CRC engine behind every patched output is measured: it reproduces the
    publisher's stamped checksums on **1,492 of 1,496** real ROMs
    (see *Validation against a real ROM library*). End-to-end patch
    compatibility is not claimed as a single number - generate it for your own
    collection with `--verify-report`.

- **📋 One-Click Preset Profiles**:
  - `📺 CRT Authentic`: Preserves original N64 blur for CRT displays.
  - `✨ Modern Crisp`: Disables VI Anti-Aliasing and Dithering for sharp edges on flat screens.
  - `🚀 Modern 4K`: Enables 640x480 Hi-Res VI Mode Table Engine & No-AA for 4K / FPGA.
  - `⚡ Speedrun Safe`: Minimal non-intrusive patches; preserves standard resolution and logic.

- **💾 Flashcart & EverDrive Compatibility Tools**:
  - **Scene-Header Stripper**: Automatically detects and strips obsolete 512/1024-byte scene release headers (`iN0000`, `PARADOX`, etc.) so `.xdelta` patches and cover arts match cleanly.
  - **CRC1 / CRC2 Checksum Repairer**: Recalculates and updates N64 boot checksums to prevent blackscreen boots on real hardware — via `rn64crc.exe` when it is runnable *and* its output actually verifies, otherwise via the **built-in pure-Python CRC engine** (macOS/Linux included).

- **📦 Archive & Community Patch Support**:
  - **Direct Archive Support**: Processes `.zip` and `.7z` archives directly.
  - **IPS & BPS Patching**: Applies `.ips` and `.bps` community patches seamlessly.

- **🔥 SubDrag `.xdelta` Community Patch Integration**:
  - Automatically detects and applies verified high-res patches for *Super Mario 64*, *GoldenEye 007*, *Banjo-Kazooie*, *F-Zero X*, *Forsaken 64*, *Pokemon Snap*, *Quake II*, and *Golden Nugget 64*.

- **🚀 Multi-Threaded Batch Runner**:
  - Parallel ThreadPoolExecutor batch processing. A bad file cannot crash the run, output is logged from a single thread, and Ctrl+C stops the batch while keeping everything already finished.

- **🛡️ 100% Non-Destructive**:
  - Original ROMs are **never modified or overwritten**. Always outputs a new tagged file (` [HR+NoAA].z64`).

---

## ⌨️ Command-Line Interface

The same patch engine is available headless as `n64patcher`
(equivalently `python -m n64patcher`):

```bash
# List the preset profiles and the keys --preset accepts
n64patcher --list-presets

# Show what would be done without writing any files
n64patcher "D:\N64 ROMs" --preset modern_4k -r --dry-run

# Patch a directory using the Modern 4K preset
n64patcher "D:\N64 ROMs" --preset modern_4k -r

# Write all outputs to a separate directory
n64patcher "D:\N64 ROMs" --preset modern_crisp -r -o "D:\Patched"

# Batch patch directly from a ZIP or 7z archive
n64patcher roms.zip --preset modern_crisp

# Verify every output and write a publishable pass/fail matrix
n64patcher "D:\N64 ROMs" -r --preset modern_4k --verify --verify-report matrix.csv

# Apply flashcart tools (strip scene headers + repair CRC checksums)
n64patcher "D:\N64 ROMs" -r --strip-header --fix-crc --preset speedrun

# Apply a custom .ips or .bps community patch to all ROMs
# (applied to the CLEAN ROM; community patches target pristine dumps)
n64patcher "D:\N64 ROMs" --patch-file sm64_widescreen.ips

# Inspect a folder with MD5/SHA-1 hashes and export to CSV
n64patcher "D:\N64 ROMs" --inspect-only --export report.csv
```

Individual flags (`--keep-aa`, `--no-dither`, `--no-divot`, `--no-gamma`,
`--hires`) override the selected preset. `--fix-crc` also creates `[CRCFIX]`
copies of ROMs the engine skips (flashcart repair mode). `--verify` exits
with code 1 if any output fails its checks, which makes it usable as a gate
in scripts. Run `n64patcher --help` for the full list.

---

## 📸 Screenshots & GUI

![Universal N64 Smart Patcher GUI](gui_screenshot.png)

Features a PyQt6 dark-mode interface with preset dropdowns, Flashcart checkboxes, tabbed execution logs, and property tooltips.

---

## 🚀 Quick Start

**Portable EXE (Windows)**

1. Download the latest standalone executable from the **[Releases](../../releases)** tab.
2. Double-click `N64_Smart_Patcher.exe` (no installation required).
3. Drag & drop your N64 ROMs (`.z64`, `.n64`, `.v64`, `.zip`, `.7z`) or an entire folder.
4. Pick a preset profile or set custom options and click **🚀 Start patching**.

**From source (any platform)**

```bash
pip install -e ".[gui]"   # omit [gui] for the CLI only - it needs no dependencies
n64patcher-gui            # desktop app
n64patcher --help         # command line
```

---

## 🛠️ Building & Architecture

```bash
# Clone repository
git clone https://github.com/inf1nit3/N64-Universal-Smart-Patcher.git
cd N64-Universal-Smart-Patcher

# Install. The engine and CLI have NO third-party dependencies;
# the GUI and .7z support are optional extras.
pip install -e .              # engine + CLI
pip install -e ".[gui]"       # + PyQt6 desktop app
pip install -e ".[archive]"   # + .7z archive support
pip install -e ".[all]"       # both
pip install -e ".[dev]"       # everything + pytest/ruff/mypy/pyinstaller

# Run the unit suite (synthetic ROMs, no game files needed)
python -m pytest

# Lint and type-check
python -m ruff check .
python -m mypy

# Run
n64patcher --help             # CLI  (also: python -m n64patcher)
n64patcher-gui                # GUI  (requires the [gui] extra)

# Build release executables (GUI + CLI) and wheel/sdist
powershell -ExecutionPolicy Bypass -File build_release.ps1   # Windows
./build_release.sh                                           # macOS / Linux
```

### Verifying your own results

`--verify` re-opens every patched file and checks it independently of the
code that produced it: the image is a recognizable ROM, the CIC boot chip is
identifiable, and the boot checksums recompute to the values stored in the
header. Failures set exit code 1.

```bash
n64patcher ~/roms --hires --verify --verify-report matrix.csv
```

`--verify-report` writes one row per ROM with the input MD5/SHA-1, what was
applied and whether it verified — a compatibility matrix you can publish
without distributing any ROM data.

### Modular System Architecture

Installable package under `src/n64patcher/` (src layout, so tests run against
the installed package rather than the working directory):

- `n64_core.py` — Core patch engine, struct VI scanner, SubDrag xdelta, inspection, **pure-Python N64 boot CRC engine** (CIC detection + CRC1/CRC2), post-patch verification.
- `header_utils.py` — Scene header detector/stripper & CRC checksum repairer (`rn64crc.exe` when runnable *and* verified, native engine otherwise).
- `presets.py` — Preset profile definitions and warning validators.
- `zip_handler.py` — Hardened archive handling for `.zip` and `.7z` (zip-slip protected, streaming size cap, compression-ratio limit, symlink members rejected).
- `ips_bps_patcher.py` — Community `.ips` & `.bps` delta patcher (spec-correct BPS with CRC32 verification; IPS sources are format-checked and byte-order corrected).
- `batch_runner.py` — Multi-threaded ThreadPoolExecutor engine with cooperative cancellation and single-threaded log draining.
- `mmap_vi_scanner.py` — Memory-mapped fast VI table scanner (used by inspection).
- `gui.py` — PyQt6 GUI with preset controls, background inspector table, drag & drop & thread exception safety.
- `cli.py` — Headless CLI runner (`n64patcher`).
- `tests/` — Synthetic ROM unit suite, 158 tests, no game files required.

---

## 📜 Version History

- **v3.2.0 (Production Hardening)**:
  - **SubDrag patches are matched on CRC1/CRC2**, not the ROM's internal title. Title matching was wrong for two of the eight supported games - the keys `BANJO KAZOOIE` and `FORSAKEN 64` never matched the real titles `Banjo-Kazooie` and `Forsaken`, so those two silently never received their patch. Each checksum pair was derived by actually applying the delta to candidate dumps. This also pins revisions: the Banjo delta targets Rev A only.
  - **Flashcart CRC repair actually repairs**: `fix_rom_crc` reported success while leaving the checksums untouched, because `rn64crc` exits 0 even when it fails. Affected `--fix-crc` and the GUI flashcart checkbox.
  - **Fully English UI**: GUI labels, CLI help and messages, and the remaining German docstrings were translated.
  - **Packaging**: installable `n64patcher` package (src layout), console entry points `n64patcher` / `n64patcher-gui`, and a dependency split — the engine and CLI now install with **no third-party dependencies**; PyQt6 and py7zr are extras.
  - **Data-loss fixes**: output filenames no longer collide (long names keep a digest instead of being truncated to a shared prefix; `(2)`, `(3)`... disambiguate; names are claimed atomically so parallel workers cannot race).
  - **CRC engine**: corrected an off-by-equality in the mixing loop (`a2 == d` took the wrong branch, producing a wrong CRC2 with a correct CRC1); byte-swapped `.v64`/`.n64` images are now handled and keep their byte order; verified against an independent reference implementation across all 9 supported CIC variants.
  - **External tools are no longer trusted on exit code alone**: `rn64crc` exits 0 even when it cannot identify the boot chip and leaves the header untouched — outputs are now checked and fall back to the native engine. Same for `u64aap`, which was detected by grepping stdout for an English phrase.
  - **Dynamic VI patcher** is bounded to the code segment (`0x1000`–8 MB), requires word alignment, and aborts on implausible match density. It previously rewrote every 4-byte match in the whole ROM, including IPL3 bootcode — which breaks CIC identification outright.
  - **Archive hardening**: the extraction cap is enforced on bytes as they arrive rather than on attacker-controlled declared sizes; compression-ratio limit; symlink members rejected; members are written to the validated path instead of letting the extractor choose.
  - **Concurrency**: batch runs are cancellable (Ctrl+C included, keeping finished work), log output is drained on a single thread, and working files no longer land in a possibly read-only input directory.
  - **New**: `--verify` / `--verify-report` re-check every output independently and emit a publishable pass/fail matrix (hashes + result, no ROM data).
  - **Tooling**: `ruff` and `mypy` clean and enforced in CI; test suite grown to 158; CI additionally proves a dependency-free install and that the wheel actually contains the bundled helpers.
  - Also fixed: IPS patches applied to byte-swapped ROMs without complaint; `no_aa` was OR'd with `no_dither`, so dither-patched ROMs were skipped as fully patched; `header_utils` reported a 3-character game code where the core reported 4.
- **v3.1.0 (Quality & Cross-Platform)**:
  - Added built-in pure-Python N64 boot CRC engine (CIC 6101-6106 + 64DD/Aleck64 detection) — CRC1/CRC2 repair now works on macOS/Linux.
  - Rewrote BPS patcher to the reference spec (fixed VLV decode, copy commands) with full CRC32 verification; hardened IPS patcher.
  - Fixed swapped country/version header bytes; fixed ignored `--output-dir`; preset/flag overrides now behave as documented; community patches apply to clean ROMs.
  - GUI: background Inspector table with CSV/JSON export, drag & drop, tool status bar, safe shutdown, persistent file logging.
  - Hardened archive extraction (zip-slip protection, size cap, unique temp dirs).
  - mmap VI scanner wired into inspection; `--dry-run`/`--version` CLI flags; CI on ubuntu/windows/macos; test suite grown to 91 tests. See `CHANGELOG.md`.
- **v3.0.0 (Major Upgrade)**:
  - Added Preset Profiles (`CRT Authentic`, `Modern Crisp`, `Modern 4K`, `Speedrun Safe`).
  - Added Scene-Header Stripper (`iN0000`, `PARADOX`, etc.).
  - Added EverDrive & Flashcart CRC1/CRC2 Checksum Repairer (`rn64crc.exe`).
  - Added direct `.zip` and `.7z` archive extraction & processing.
  - Added `.ips` and `.bps` community patch support.
  - Added Multi-threaded batch processing engine (`ThreadPoolExecutor`).
  - Added bulletproof Qt Thread exception handling to prevent batch crashes.
- **v2.0.0**:
  - Introduced Smart VI Mode Table Engine v2.0 (structural table matching instead of blind instruction search).
  - Integrated SubDrag `.xdelta` verified patches for 8 major titles.
  - Built PyQt6 GUI and initial CLI.
- **v1.0.0**:
  - Initial proof of concept with `u64aap.exe` and header inspection.

---

### Validation against a real ROM library

The pure-Python CRC engine was checked against a 1,549-file library. For every
image the engine recomputed CRC1/CRC2 and compared them to the values the
publisher stamped in the header:

| | |
|---|---|
| Files scanned | 1,549 |
| Recognized N64 images | 1,527 |
| CIC boot chip identified | 1,519 |
| ...of those, carrying stamped checksums | 1,496 |
| **Checksums reproduced exactly** | **1,492 (99.73%)** |

CIC coverage in that set: 6102 (1,332), 6103 (92), 6105 (51), 6101 (34),
6106 (10). The 23 images with all-zero checksums were never stamped
(iQue dumps, homebrew, dev builds) and are excluded rather than counted as
failures. Four genuine disagreements remain: a GameShark Pro cartridge, a
Derby Stallion 64 beta, Human Grand Prix, and one ROM previously patched by
an older version of this tool.

This measures the CRC engine specifically, not end-to-end patch
compatibility. Reproduce it on your own library with `--verify-report`.

---

## 🔬 Technical Details: Smart VI Mode Table Engine

Blind binary search-and-replace for MIPS instructions (`addiu $t6, $zero, 320`) can corrupt game logic, because the same four bytes occur in compressed asset data as often as in code.

Our **Smart VI Engine** searches for N64 SDK `OSViMode` struct data definitions:

```
[VI_CTRL] [WIDTH = 0x00000140] [BURST = 0x03E52239 (NTSC) / 0x0404233A (PAL)] ...
```

The burst constants are hardware timing values specific to Video Interface
register configuration, so requiring one immediately after a width field is a
far stronger signal than the width alone. This is a structural heuristic, not a
proof: an eight-byte coincidence in asset data is possible in principle, and no
exhaustive survey has been run to rule it out. Two things bound the risk in
practice — the pairing requirement itself, and `--verify`, which re-inspects
every output and reports whether the CRC validates and the VI tables ended up in
the expected state.

The separate *dynamic instruction patcher* (the No-AA fallback used when
`u64aap` has no database entry) is a genuine pattern rewrite. It is restricted
to the code segment (ROM offset `0x1000` to 8 MB), requires word alignment, and
aborts if it finds an implausible number of match sites — see
`apply_dynamic_vi_patch`.

---

## 🙏 Credits, Tools & Acknowledgments

- **`u64aap` (N64 Anti-Aliasing Patcher)** by **saturnu**
- **`rn64crc` (N64 Checksum Recalculator)** by **saturnu**
- **`xdelta3` (VCDIFF Delta Compression)** by **Josh MacDonald** ([github.com/jmacd/xdelta](https://github.com/jmacd/xdelta))
- **SubDrag**: High-resolution 640x480i N64 ROM patches and `Make_HiRes` research (`jombo23/N64-Tools`).
- **Zoinkity & Trevor**: N64 hex editing techniques and video timing research.
- **jombo23**: Archived N64 tools and community patches.
- **Admentus64**: N64 video pipeline documentation (`Patcher64+ Tool`).
- **PyQt6 & PyInstaller**: GUI framework and packaging tools.
- **Nintendo**: Creators of the Nintendo 64 hardware and `libultra` OS architecture.

---

## 🤖 Built with Vibecoding & AI-Pair Engineering

This application was developed using **Vibecoding** — a modern AI-assisted software engineering methodology combining human domain vision and rapid agentic AI pair-programming.

### 🛠️ AI Development Toolchain:
- **AI Coding Agent**: **Google DeepMind Antigravity Agentic AI**
- **LLM Engines**: **Claude Opus 5**, **Qwen 3.8 Max**, **Kimi K3**, **Gemini 3.6 Flash** & **Claude 3.5 / 4.6 Thinking**
- **Image Synthesis**: Antigravity `generate_image` (retro-futuristic 3D N64 app icon)
- **Deployment Automation**: GitHub CLI (`gh`), Git, and PyInstaller

The v3.2 hardening pass — the CRC engine correction, the output-collision and
external-tool-trust fixes, the package restructure and the SubDrag CRC table —
was carried out with **Claude Opus 5** in Claude Code, working against a real
1,549-ROM library rather than synthetic fixtures alone.

---

## 🔍 Transparency & Verifiability

**100% of everything this tool does is fully verifiable:**
1. **Full Open-Source Code**: Every line is open source under [`src/n64patcher/`](src/n64patcher) — the engine in [`n64_core.py`](src/n64patcher/n64_core.py), the GUI in [`gui.py`](src/n64patcher/gui.py).
2. **Detailed Execution Logs**: Execution logs are saved to `%APPDATA%\N64SmartPatcher\N64_Patcher_Log.txt`.
3. **Hex & Binary Diff Auditing**: Originals are never overwritten; compare files with `fc /b` or HxD.
4. **Self-Inspecting ROM Tree**: Drag patched ROMs back in to audit header CRCs and VI statuses.
5. **Independent Re-Verification**: `--verify` re-opens each output and re-derives its checksums from the data, rather than trusting the code that wrote it. `--verify-report` emits the per-ROM result as CSV/JSON.

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).
