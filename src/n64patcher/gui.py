"""
n64patcher.gui - PyQt6 desktop interface
Universal N64 ROM Inspector & Smart Patcher - PyQt6 GUI

Design notes:
  - PatchWorker emits 'done' rather than shadowing QThread's own
    'finished' signal; the worker is stopped cleanly on close.
  - Inspection runs on a background thread and fills a QTreeWidget table
    (title, region, CRC1/CRC2, hashes, ...). The verified-hi-res scan and
    the Saves tab run on their own workers for the same reason.
  - Drag & drop accepts files, folders and archives; save files and
    folders containing them are routed to the Saves tab.
  - The status bar shows tool availability; log lines are additionally
    written to the persistent log file (core.append_log).
  - Extraction temp directories live in the system temp area and are
    cleaned up on close even when no patch run happened.
  - A menu bar and keyboard shortcuts drive the same slots as the
    buttons; nothing is reachable only by mouse.
"""

import os
import sys
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import datdb, savegame, theme
from . import manifest as manifest_mod
from . import n64_core as core
from .header_utils import detect_and_strip_scene_header, fix_rom_crc
from .presets import apply_preset, get_preset_warnings, list_presets
from .zip_handler import (
    cleanup_temp_dir,
    create_extraction_dir,
    extract_roms_from_archive,
    is_archive,
)


class PatchWorker(QThread):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(dict)  # deliberately NOT 'finished' (collides with QThread's)
    log_message = pyqtSignal(str)

    def __init__(
        self,
        roms: list[str],
        options: "core.PatchOptions",
        strip_header: bool = False,
        fix_crc: bool = False,
        output_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.roms = roms
        self.options = options
        self.strip_header = strip_header
        self.fix_crc = fix_crc
        self.output_dir = output_dir or None
        self.should_cancel = False
        self.log_lines: list[str] = []

    def cancel(self) -> None:
        self.should_cancel = True

    def _log(self, msg: object) -> None:
        self.log_lines.append(str(msg))
        self.log_message.emit(str(msg))

    def run(self) -> None:
        results: dict[str, Any] = {"patched": 0, "skipped": 0, "errors": 0, "details": []}
        total = len(self.roms)

        for i, rom in enumerate(self.roms, 1):
            if self.should_cancel:
                self._log("⛔ Cancelled by user")
                break

            filename = os.path.basename(rom)
            self.progress.emit(i, total, filename)
            working_rom = rom
            temp_stripped = None

            try:
                # Strip the scene header first
                if self.strip_header:
                    temp_stripped = rom + ".stripped.z64"
                    header_result = detect_and_strip_scene_header(rom, temp_stripped)
                    if header_result.get("stripped"):
                        self._log(f"🔧 Header stripped: {filename}")
                        working_rom = temp_stripped

                # Patching
                result = core.patch_rom(
                    working_rom,
                    self.options,
                    log=lambda m: self._log(f"   {m}"),
                    should_cancel=lambda: self.should_cancel,
                    output_dir=self.output_dir,
                )

                if not isinstance(result, dict):
                    result = {"status": "error", "message": "Invalid patch result", "output": None}

                out_file = result.get("output")

                # Optional (idempotent) CRC pass for flashcarts
                if (
                    self.fix_crc
                    and result.get("status") == "patched"
                    and out_file
                    and os.path.isfile(out_file)
                ):
                    crc_result = fix_rom_crc(out_file)
                    self._log(f"🔧 {crc_result.get('message', 'CRC Updated')}: {filename}")

                results["details"].append(result)
                if result.get("status") == "patched":
                    results["patched"] += 1
                    out_name = os.path.basename(out_file) if out_file else "patched.z64"
                    self._log(f"✅ {filename} -> {out_name}")
                elif result.get("status") == "skipped":
                    results["skipped"] += 1
                    self._log(f"⏭️  {filename}: {result.get('message', 'Skipped')}")
                else:
                    results["errors"] += 1
                    self._log(f"❌ {filename}: {result.get('message', 'Error')}")

            except Exception as e:
                results["errors"] += 1
                self._log(f"❌ Error on {filename}: {e}")

            finally:
                # Clean up the stripped temp ROM
                if temp_stripped and os.path.isfile(temp_stripped):
                    try:
                        os.remove(temp_stripped)
                    except OSError:
                        pass

        self.done.emit(results)


class InspectWorker(QThread):
    """Background inspection so the GUI stays responsive on large
    libraries."""

    item_ready = pyqtSignal(dict)
    done = pyqtSignal(list)

    def __init__(
        self, roms: list[str], with_hashes: bool = True, dat: datdb.DatIndex | None = None
    ) -> None:
        super().__init__()
        self.roms = roms
        self.with_hashes = with_hashes
        self.dat = dat

    def run(self) -> None:
        infos = []
        for rom in self.roms:
            try:
                info = core.inspect_rom_details(rom, with_hashes=self.with_hashes, dat=self.dat)
            except Exception as e:
                info = {
                    "filename": os.path.basename(rom),
                    "path": rom,
                    "format": f"Error: {e}",
                    "title": "",
                    "region": "",
                    "size_mb": 0,
                    "no_aa": False,
                    "is_hires_640x480": False,
                    "vi_table_count": 0,
                    "crc1": "",
                    "crc2": "",
                    "has_subdrag_patch": False,
                }
            infos.append(info)
            self.item_ready.emit(info)
        self.done.emit(infos)


class HiresScanWorker(QThread):
    """Checks ROMs for a verified 640x480 patch off the UI thread.

    inspect_rom_details reads a megabyte per ROM for the boot checksum, so
    re-checking a whole library on every add froze the window for as long
    as the disk took. Results are cached by the caller; this worker only
    ever sees paths that were not scanned yet.
    """

    done = pyqtSignal(list)  # list of (path, supported) pairs

    def __init__(self, roms: list[str]) -> None:
        super().__init__()
        self.roms = list(roms)

    def run(self) -> None:
        results = []
        for rom in self.roms:
            supported = False
            try:
                info = core.inspect_rom_details(rom)
                supported = info.get("hires_support") == core.HIRES_VERIFIED
            except Exception:
                supported = False
            results.append((rom, supported))
        self.done.emit(results)


class SaveBatchWorker(QThread):
    """Runs a Saves-tab job (inspect or convert) off the UI thread.

    A dropped folder can hold hundreds of saves; reading them on the UI
    thread froze the window for the whole batch. Conversions that hit an
    existing output file are NOT handled here: overwriting somebody's
    progress is a question, and questions belong to the UI thread. The
    worker reports them as conflicts and the caller asks.
    """

    line = pyqtSignal(str)
    done = pyqtSignal(dict)

    def __init__(
        self,
        job: str,
        paths: list[str],
        source: str | None = None,
        target: str | None = None,
        out_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.job = job
        self.paths = list(paths)
        self.source = source
        self.target = target
        self.out_dir = out_dir

    def run(self) -> None:
        results: dict[str, Any] = {"job": self.job, "converted": 0, "failed": 0, "conflicts": []}
        for path in self.paths:
            name = os.path.basename(path)
            try:
                if self.job == "inspect":
                    with open(path, "rb") as f:
                        data = f.read()
                    self.line.emit(savegame.describe_file(path, data))
                    self.line.emit("")
                else:
                    res = savegame.convert_file(
                        path, self.source or "", self.target or "", out_dir=self.out_dir
                    )
                    if res["status"] == "converted":
                        results["converted"] += 1
                        note = "" if res["changed"] else "  (identical - only renamed)"
                        self.line.emit(f"✅ {name}{note}")
                        self.line.emit(f"   -> {res['output']}")
                    elif res["status"] == "exists":
                        results["conflicts"].append((path, str(res["output"])))
                    else:
                        results["failed"] += 1
                        self.line.emit(f"❌ {name}: {res['message']}")
            except OSError as exc:
                results["failed"] += 1
                self.line.emit(f"❌ {name}: {exc}")
        self.done.emit(results)


class N64PatcherGUI(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Universal N64 ROM Inspector & Smart Patcher v{core.VERSION}")
        self.setGeometry(100, 100, 1000, 760)
        self.setAcceptDrops(True)

        icon_path = core.get_asset_path("app_icon.ico")
        if not os.path.exists(icon_path):
            icon_path = core.get_asset_path("app_icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.settings = QSettings("inf1nit3", "N64SmartPatcher")
        self.rom_list: list[str] = []
        self.save_list: list[str] = []
        self.temp_dirs: list[str] = []
        self.worker: PatchWorker | None = None
        self.inspect_worker: InspectWorker | None = None
        self.last_infos: list[dict[str, Any]] = []
        # Verified-hi-res support per ROM path, filled by HiresScanWorker.
        # Keyed by path for the session: outputs are separate files, so a
        # listed ROM never changes under the cache's feet.
        self._hires_cache: dict[str, bool] = {}
        self._hires_scan_worker: HiresScanWorker | None = None
        self._dat_index: datdb.DatIndex | None = None
        self.save_worker: SaveBatchWorker | None = None

        self.init_ui()
        self._build_menus()
        self.load_settings()
        self.update_status_bar()

    # ------------------------------------------------------------------ Menus

    def _cmd_key(self, sequence: str) -> QKeySequence:
        """Map a 'Ctrl+X' shortcut to Cmd on macOS, Ctrl elsewhere."""
        if sys.platform == "darwin":
            sequence = sequence.replace("Ctrl+", "Meta+")
        return QKeySequence(sequence)

    def _build_menus(self) -> None:
        """Menu bar and keyboard shortcuts.

        Every control that drives a run is reachable from the keyboard:
        adding, inspecting, starting and cancelling. The bar carries the
        console theme like every other chrome, and its Quit/About entries
        use standard roles so macOS places them where its users expect.
        """
        bar = self.menuBar()
        assert bar is not None  # QMainWindow always provides one

        file_menu = bar.addMenu("&File")
        assert file_menu is not None
        act = QAction("Add &ROMs…", self)
        act.setShortcut(self._cmd_key("Ctrl+O"))
        act.triggered.connect(self.add_files)
        file_menu.addAction(act)
        act = QAction("Add ROM &folder…", self)
        act.setShortcut(self._cmd_key("Ctrl+Shift+O"))
        act.triggered.connect(self.add_folder)
        file_menu.addAction(act)
        act = QAction("Add save files…", self)
        act.setShortcut(self._cmd_key("Ctrl+Alt+O"))
        act.triggered.connect(self.add_save_files)
        file_menu.addAction(act)
        file_menu.addSeparator()
        act = QAction("Clear ROM list", self)
        act.triggered.connect(self.clear_list)
        file_menu.addAction(act)
        act = QAction("Clear save list", self)
        act.triggered.connect(self.clear_saves)
        file_menu.addAction(act)
        file_menu.addSeparator()
        act = QAction("&Quit", self)
        act.setShortcut(QKeySequence.StandardKey.Quit)
        act.triggered.connect(self.close)
        file_menu.addAction(act)

        action_menu = bar.addMenu("&Action")
        assert action_menu is not None
        self.act_inspect = QAction("&Inspect ROMs", self)
        self.act_inspect.setShortcut(self._cmd_key("Ctrl+I"))
        self.act_inspect.triggered.connect(self.start_inspection)
        action_menu.addAction(self.act_inspect)
        self.act_start = QAction("&Start patching", self)
        self.act_start.setShortcut(self._cmd_key("Ctrl+Return"))
        self.act_start.triggered.connect(self.start_patching)
        action_menu.addAction(self.act_start)
        self.act_cancel = QAction("Cancel", self)
        self.act_cancel.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self.act_cancel.setEnabled(False)
        self.act_cancel.triggered.connect(self.cancel_patching)
        action_menu.addAction(self.act_cancel)
        action_menu.addSeparator()
        act = QAction("&Revert a patch…", self)
        act.triggered.connect(self.revert_patch)
        action_menu.addAction(act)

        view_menu = bar.addMenu("&View")
        assert view_menu is not None
        self._tab_actions = []
        for i, label in enumerate(("Patching", "Inspector", "Saves", "Log")):
            act = QAction(f"{label} tab", self)
            act.setShortcut(self._cmd_key(f"Ctrl+{i + 1}"))
            # bind the index, not the loop variable
            act.triggered.connect(lambda _=False, idx=i: self.tabs.setCurrentIndex(idx))
            view_menu.addAction(act)
            self._tab_actions.append(act)

        help_menu = bar.addMenu("&Help")
        assert help_menu is not None
        act = QAction("&About", self)
        act.setShortcut(self._cmd_key("Ctrl+?"))
        act.triggered.connect(self._show_about)
        help_menu.addAction(act)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About N64 Smart Patcher",
            f"Universal N64 ROM Inspector & Smart Patcher\n"
            f"v{core.VERSION}\n\n"
            "Patches VI filters and verified 640x480 hi-res deltas, "
            "converts save files between emulators and flashcarts, and "
            "inspects ROM headers.\n\n"
            "The console-look theme is an original design in the idiom of "
            "mid-90s hardware; it is not affiliated with any console maker.",
        )

    # ------------------------------------------------------------------ UI

    def _build_faceplate(self) -> QFrame:
        """Front-panel strip: title plate over a four-segment colour rule.

        The rule is a plain row of coloured blocks - a common retro device,
        not a recreation of anyone's mark. See theme.py for the limits this
        styling keeps to.
        """
        plate = QFrame()
        plate.setObjectName("faceplate")
        outer = QVBoxLayout(plate)
        outer.setContentsMargins(14, 10, 14, 0)
        outer.setSpacing(8)

        row = QHBoxLayout()
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        # QLabel renders "&&" literally, so use a separator instead.
        title = QLabel("ROM INSPECTOR · SMART PATCHER")
        title.setObjectName("faceplateTitle")
        subtitle = QLabel(f"v{core.VERSION}  //  CARTRIDGE TOOLKIT")
        subtitle.setObjectName("faceplateSub")
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        row.addLayout(text_col)
        row.addStretch()
        outer.addLayout(row)

        rule = QFrame()
        rule.setObjectName("accentRule")
        rule.setFixedHeight(5)
        rule_layout = QHBoxLayout(rule)
        rule_layout.setContentsMargins(0, 0, 0, 0)
        rule_layout.setSpacing(0)
        for colour in theme.ACCENTS:
            seg = QFrame()
            seg.setStyleSheet(f"background-color: {colour}; border: none;")
            rule_layout.addWidget(seg)
        outer.addWidget(rule)
        return plate

    def init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(self._build_faceplate())

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Patching
        patch_tab = QWidget()
        patch_layout = QVBoxLayout(patch_tab)
        self.tabs.addTab(patch_tab, "🎮 Patching")

        preset_group = QGroupBox("📋 Choose a preset profile")
        self._accent_group(preset_group, 0)
        preset_layout = QVBoxLayout()

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("⚙️ Custom (individual settings)", "custom")
        for preset in list_presets():
            self.preset_combo.addItem(f"{preset['name']} - {preset['description']}", preset["key"])
        self.preset_combo.currentIndexChanged.connect(self.on_preset_changed)
        preset_layout.addWidget(self.preset_combo)

        self.preset_warning_label = QLabel("")
        self.preset_warning_label.setStyleSheet(f"color: {theme.DANGER}; font-weight: bold;")
        self.preset_warning_label.setWordWrap(True)
        self.preset_warning_label.setVisible(False)
        preset_layout.addWidget(self.preset_warning_label)

        preset_group.setLayout(preset_layout)
        patch_layout.addWidget(preset_group)

        options_group = QGroupBox("🎨 Visual filters (individual settings)")
        self._accent_group(options_group, 1)
        options_layout = QVBoxLayout()
        self.cb_no_aa = QCheckBox("Remove anti-aliasing (No-AA) - sharper edges")
        self.cb_no_dither = QCheckBox("Remove dither filter - no 16-bit artifacts")
        self.cb_no_divot = QCheckBox("Remove divot filter - no edge blurring")
        self.cb_no_gamma = QCheckBox("Remove gamma boost - accurate colors")
        self.cb_hires = QCheckBox("High-Res 640x480 (Smart VI Table Engine)")
        for cb in [
            self.cb_no_aa,
            self.cb_no_dither,
            self.cb_no_divot,
            self.cb_no_gamma,
            self.cb_hires,
        ]:
            options_layout.addWidget(cb)
        options_group.setLayout(options_layout)
        patch_layout.addWidget(options_group)

        flashcart_group = QGroupBox("💾 Flashcart options")
        self._accent_group(flashcart_group, 2)
        flashcart_layout = QVBoxLayout()
        self.cb_strip_header = QCheckBox("Strip scene header (iN0000 etc.)")
        self.cb_fix_crc = QCheckBox("Repair CRC1/CRC2 (EverDrive compatible)")
        self.cb_manifest = QCheckBox("Write undo manifest next to each output")
        self.cb_manifest.setToolTip(
            "Records every byte run a run changed, as a .n64patch.json "
            "sidecar. Needed to undo a patch later; the input ROM is never "
            "relied on for that."
        )
        flashcart_layout.addWidget(self.cb_strip_header)
        flashcart_layout.addWidget(self.cb_fix_crc)
        flashcart_layout.addWidget(self.cb_manifest)
        flashcart_group.setLayout(flashcart_layout)
        patch_layout.addWidget(flashcart_group)

        output_group = QGroupBox("📤 Output location")
        self._accent_group(output_group, 4)
        output_layout = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setReadOnly(True)
        self.output_dir_edit.setPlaceholderText(
            "Default: next to each ROM (tagged, never over the input)"
        )
        self.output_dir_edit.setToolTip(
            "Where patched ROMs are written.\n\n"
            "Empty means next to each input ROM; the output name always "
            "carries a tag like [NoAA] or [640x480] so the input is never "
            "overwritten."
        )
        self.btn_output_browse = QPushButton("📂 Browse…")
        self.btn_output_reset = QPushButton("↩️ Default")
        self.btn_output_browse.clicked.connect(self.choose_output_dir)
        self.btn_output_reset.clicked.connect(self.reset_output_dir)
        output_layout.addWidget(self.output_dir_edit, 1)
        output_layout.addWidget(self.btn_output_browse)
        output_layout.addWidget(self.btn_output_reset)
        output_group.setLayout(output_layout)
        patch_layout.addWidget(output_group)

        # No bare "&" in a QGroupBox title: Qt reads it as a mnemonic marker
        # and renders "drag & drop" as "drag _drop".
        list_group = QGroupBox("📁 ROM library (drag and drop supported)")
        self._accent_group(list_group, 3)
        list_layout = QVBoxLayout()
        self.rom_list_widget = QListWidget()
        list_layout.addWidget(self.rom_list_widget)

        btn_layout = QHBoxLayout()
        self.btn_add_files = QPushButton("➕ Add files")
        self.btn_add_folder = QPushButton("📂 Add folder")
        self.btn_clear = QPushButton("🗑️ Clear list")
        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_add_folder.clicked.connect(self.add_folder)
        self.btn_clear.clicked.connect(self.clear_list)
        btn_layout.addWidget(self.btn_add_files)
        btn_layout.addWidget(self.btn_add_folder)
        btn_layout.addWidget(self.btn_clear)
        list_layout.addLayout(btn_layout)
        list_group.setLayout(list_layout)
        patch_layout.addWidget(list_group)

        self.progress_label = QLabel("")
        patch_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        patch_layout.addWidget(self.progress_bar)

        action_layout = QHBoxLayout()
        self.btn_inspect = QPushButton("🔍 Inspect (table)")
        self.btn_cancel = QPushButton("⛔ Cancel")
        self.btn_cancel.setObjectName("dangerAction")
        self.btn_cancel.setEnabled(False)

        # Round red START key, centred between the two flanking controls -
        # the layout a 90s pad used. The label stays plain text so screen
        # readers and tests still see a normal button.
        self.btn_patch = QPushButton("START")
        self.btn_patch.setObjectName("startButton")
        self.btn_patch.setFixedSize(82, 82)
        self.btn_patch.setToolTip("Start patching the loaded ROMs")
        self.btn_patch.setAccessibleName("Start patching")
        self.btn_patch.setCursor(Qt.CursorShape.PointingHandCursor)

        collar = QFrame()
        collar.setObjectName("startCollar")
        collar.setFixedSize(104, 104)
        collar_layout = QVBoxLayout(collar)
        collar_layout.setContentsMargins(0, 0, 0, 0)
        collar_layout.addWidget(self.btn_patch, 0, Qt.AlignmentFlag.AlignCenter)

        self.btn_inspect.clicked.connect(self.start_inspection)
        self.btn_patch.clicked.connect(self.start_patching)
        self.btn_cancel.clicked.connect(self.cancel_patching)
        action_layout.addWidget(self.btn_inspect)
        action_layout.addWidget(collar, 0, Qt.AlignmentFlag.AlignCenter)
        action_layout.addWidget(self.btn_cancel)
        patch_layout.addLayout(action_layout)

        # Tab 2: Inspector
        inspect_tab = QWidget()
        inspect_layout = QVBoxLayout(inspect_tab)
        self.tabs.addTab(inspect_tab, "🔍 Inspector")

        inspect_ctrl = QHBoxLayout()
        self.cb_hashes = QCheckBox("Compute MD5/SHA-1 (slow)")
        self.cb_hashes.setChecked(True)
        self.cb_dats = QCheckBox("Identify against No-Intro/Redump DATs")
        self.cb_dats.setChecked(True)
        self.cb_dats.setToolTip(
            "Matches each ROM's hashes against DAT files in "
            "~/.n64patcher/dats/ (or the folder chosen via Browse).\n\n"
            "A hit proves the dump is byte-exact and names the game; "
            "without a DAT the inspector can only read the header."
        )
        self.btn_dat_browse = QPushButton("📂 DAT folder…")
        self.btn_dat_browse.clicked.connect(self.choose_dat_dir)
        self.btn_revert = QPushButton("↩️ Revert a patch…")
        self.btn_revert.clicked.connect(self.revert_patch)
        self.btn_revert.setToolTip(
            "Undo a patched ROM using its .n64patch.json sidecar, "
            "recovering the exact original bytes."
        )
        self.btn_export_csv = QPushButton("💾 Export CSV")
        self.btn_export_json = QPushButton("💾 Export JSON")
        self.btn_export_csv.clicked.connect(lambda: self.export_report("csv"))
        self.btn_export_json.clicked.connect(lambda: self.export_report("json"))
        self.btn_export_csv.setEnabled(False)
        self.btn_export_json.setEnabled(False)
        inspect_ctrl.addWidget(self.cb_hashes)
        inspect_ctrl.addWidget(self.cb_dats)
        inspect_ctrl.addWidget(self.btn_dat_browse)
        inspect_ctrl.addStretch(1)
        inspect_ctrl.addWidget(self.btn_revert)
        inspect_ctrl.addWidget(self.btn_export_csv)
        inspect_ctrl.addWidget(self.btn_export_json)
        inspect_layout.addLayout(inspect_ctrl)

        self.inspect_progress = QProgressBar()
        self.inspect_progress.setVisible(False)
        inspect_layout.addWidget(self.inspect_progress)

        self.tree = QTreeWidget()
        # setHeaderLabels below fixes the count at the 16 labels it is given.
        self.tree.setHeaderLabels(
            [
                "File",
                "Title",
                "Region",
                "Format",
                "Size (MB)",
                "Resolution",
                "AA",
                "VI tables",
                "640x480",
                "CRC1",
                "CRC2",
                "SubDrag patch",
                "MD5",
                "SHA1",
                "DAT match",
                "Dump",
            ]
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        inspect_layout.addWidget(self.tree)

        # Tab 3: Saves
        self.tabs.addTab(self._build_save_tab(), "💾 Saves")

        # Tab 4: Log
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.tabs.addTab(log_tab, "📜 Log")

        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumBlockCount(10000)
        self.log_widget.setFont(QFont("Menlo", 9))
        log_layout.addWidget(self.log_widget)

        # Status bar
        self.status_bar = self.statusBar()
        assert self.status_bar is not None  # QMainWindow always provides one
        self.status_led = QLabel("\u25cf")
        self.status_bar.addWidget(self.status_led)
        self._set_led("idle")
        self.status_tool_label = QLabel("")
        self.status_count_label = QLabel("")
        self.status_bar.addWidget(self.status_tool_label)
        self.status_bar.addPermanentWidget(self.status_count_label)

        self.log(f"🎮 N64 Smart Patcher v{core.VERSION} started")
        self.log(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log(f"📂 Log file: {core.get_log_path()}")

    # ------------------------------------------------------- Presets

    @staticmethod
    def _accent_group(group: QGroupBox, index: int) -> QGroupBox:
        """Give a section a coloured spine, like a cartridge label band."""
        colour = theme.accent_for(index)
        group.setStyleSheet(
            f"QGroupBox {{ border-left: 5px solid {colour}; }}"
            f"QGroupBox::title {{ color: {colour}; }}"
        )
        return group

    def _set_led(self, state: str) -> None:
        """Front-panel indicator: idle, working, done, or trouble."""
        colour = {
            "idle": theme.LABEL_DIM,
            "busy": theme.ACCENT_YELLOW,
            "ok": theme.ACCENT_GREEN,
            "error": theme.ACCENT_RED,
        }.get(state, theme.LABEL_DIM)
        tip = {
            "idle": "Idle",
            "busy": "Working",
            "ok": "Last run finished cleanly",
            "error": "Last run reported errors",
        }.get(state, "Idle")
        self.status_led.setStyleSheet(f"color: {colour}; font-size: 14px; background: transparent;")
        self.status_led.setToolTip(tip)

    def _set_preset_warning(self, text: str) -> None:
        """Show the warning line only when there is one; an empty label
        still reserves vertical space."""
        self.preset_warning_label.setText(text)
        self.preset_warning_label.setVisible(bool(text))

    def on_preset_changed(self, index: int) -> None:
        preset_key = self.preset_combo.currentData()

        if preset_key == "custom":
            self._set_preset_warning("")
            for cb in [
                self.cb_no_aa,
                self.cb_no_dither,
                self.cb_no_divot,
                self.cb_no_gamma,
                self.cb_hires,
            ]:
                cb.setEnabled(True)
            # Re-assert the hi-res gate; "custom" just re-enabled everything.
            self.update_hires_availability()
        else:
            options = apply_preset(preset_key)
            self.cb_no_aa.setChecked(options.no_aa)
            self.cb_no_dither.setChecked(options.no_dither)
            self.cb_no_divot.setChecked(options.no_divot)
            self.cb_no_gamma.setChecked(options.no_gamma)
            self.cb_hires.setChecked(options.hires)

            for cb in [
                self.cb_no_aa,
                self.cb_no_dither,
                self.cb_no_divot,
                self.cb_no_gamma,
                self.cb_hires,
            ]:
                cb.setEnabled(False)

            warnings = list(get_preset_warnings(preset_key))
            # A preset asking for hi-res on ROMs that cannot take it would
            # otherwise show a ticked box that quietly does nothing. While
            # a scan is still running the answer is not known yet, so the
            # warning waits rather than guessing.
            if options.hires and self.rom_list and not self._hires_scan_pending():
                warnings.append(
                    "640x480 will be skipped: none of the loaded ROMs has a "
                    "verified patch (widening alone breaks rendering)."
                )
            if warnings:
                self._set_preset_warning("\n".join(f"⚠️ {w}" for w in warnings))
            else:
                self._set_preset_warning("")

    # -------------------------------------------------- Drag & Drop

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        mime = event.mimeData()
        if mime is None:
            return
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        self.add_paths(paths)

    # ------------------------------------------------- ROM management

    def add_paths(self, paths: list[str]) -> None:
        saves = 0
        for path in paths:
            try:
                if os.path.isdir(path):
                    saves += self._add_folder_contents(path)
                elif is_archive(path):
                    self._add_archive(path)
                elif core.is_rom_file(path) and not core.is_tool_output(path):
                    self._add_rom(path)
                elif savegame.is_save_file(path):
                    # Dropping a save on the window is unambiguous - no ROM
                    # carries these extensions - so it goes to the Saves tab
                    # rather than being silently ignored.
                    saves += self.add_saves([path])
            except Exception as e:
                self.log(f"⚠️ Error adding {path}: {e}")
        if saves:
            self.log(f"💾 {saves} save file(s) added to the Saves tab")
        self.update_status_bar()
        self.update_hires_availability()

    def _add_rom(self, path: str) -> None:
        if path not in self.rom_list:
            self.rom_list.append(path)
            self.rom_list_widget.addItem(os.path.basename(path))

    def _add_archive(self, path: str) -> None:
        self.log(f"📦 Extracting archive: {os.path.basename(path)}")
        temp_dir = create_extraction_dir()
        try:
            extracted = extract_roms_from_archive(path, temp_dir)
        except RuntimeError as e:
            cleanup_temp_dir(temp_dir)
            self.log(f"⚠️ {e}")
            return
        self.temp_dirs.append(temp_dir)
        for rom in extracted:
            self._add_rom(rom)
        self.log(f"   ✓ {len(extracted)} ROM(s) extracted")

    def _add_folder_contents(self, folder: str) -> int:
        """Add everything recognisable below *folder*; returns the number
        of save files routed to the Saves tab. The CLI batch already walks
        folders for saves, so the GUI dropping them was a parity gap."""
        saves = 0
        for root, _, files in os.walk(folder):
            for file in files:
                full_path = os.path.join(root, file)
                try:
                    if is_archive(full_path):
                        self._add_archive(full_path)
                    elif core.is_rom_file(file) and not core.is_tool_output(full_path):
                        self._add_rom(full_path)
                    elif savegame.is_save_file(full_path):
                        saves += self.add_saves([full_path])
                except Exception as e:
                    self.log(f"⚠️ Error on file {file}: {e}")
        return saves

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select ROM files",
            "",
            "N64 ROMs & archives (*.z64 *.v64 *.n64 *.zip *.7z);;All files (*)",
        )
        self.add_paths(files)
        self.log(f"📊 {len(self.rom_list)} ROM(s) in the list")

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            self.add_paths([folder])
            self.log(f"📊 {len(self.rom_list)} ROM(s) in the list")

    def choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Where should patched ROMs go?")
        if folder:
            self.output_dir_edit.setText(folder)
            self.settings.setValue("output_dir", folder)
            self.log(f"📤 Patched ROMs will be written to {folder}")

    def reset_output_dir(self) -> None:
        self.output_dir_edit.clear()
        self.settings.remove("output_dir")
        self.log("📤 Output location back to default (next to each ROM)")

    def _patch_output_dir(self) -> str | None:
        """The chosen output directory, or None for 'next to each ROM'."""
        return self.output_dir_edit.text().strip() or None

    def choose_dat_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder holding No-Intro/Redump .dat files")
        if folder:
            self.settings.setValue("dat_dir", folder)
            self._dat_index = None  # force a reload with the new folder
            self.log(f"📚 DAT folder set to {folder}")

    def _load_dat_index(self) -> datdb.DatIndex | None:
        """The DAT index for this run, loaded once and cached.

        Returns None when no DAT resolves, so the inspector falls back to
        reading headers alone - the same degradation the CLI has.
        """
        if self._dat_index is None:
            problems: list[str] = []
            explicit: list[str] | None = None
            folder = self.settings.value("dat_dir", "", type=str)
            if folder and os.path.isdir(folder):
                explicit = [folder]
            try:
                index = datdb.load_dats(explicit, on_error=problems.append)
            except Exception as e:  # a broken DAT must not kill inspection
                self.log(f"⚠️ DAT lookup disabled: {e}")
                return None
            for problem in problems:
                self.log(f"⚠️ {problem}")
            if not index:
                return None
            self.log(f"📚 {datdb.describe(index).splitlines()[0]}")
            self._dat_index = index
        return self._dat_index or None

    def revert_patch(self) -> None:
        """Undo one patched ROM through its .n64patch.json sidecar."""
        target, _ = QFileDialog.getOpenFileName(
            self,
            "Select a patched ROM (needs its .n64patch.json sidecar)",
            "",
            "N64 ROMs (*.z64 *.v64 *.n64);;All files (*)",
        )
        if not target:
            return
        sidecar = manifest_mod.manifest_path_for(target)
        if not os.path.isfile(sidecar):
            QMessageBox.warning(
                self,
                "No manifest",
                f"{os.path.basename(target)} has no .n64patch.json sidecar.\n\n"
                "Either it was patched without manifests, or the sidecar "
                "was moved. The input ROM was never modified - it is the "
                "original.",
            )
            return
        try:
            man = manifest_mod.load_manifest(sidecar)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Manifest unreadable", str(e))
            return

        self.log(f"\n↩️ Manifest for {os.path.basename(target)}:")
        for line in manifest_mod.describe(man).splitlines():
            self.log(f"   {line}")
        if not man.get("revertible"):
            QMessageBox.warning(
                self,
                "Cannot revert",
                man.get("revert_note") or "The manifest holds no original bytes.",
            )
            return

        base = os.path.basename(target)
        stem = os.path.splitext(base)[0]
        suggested = os.path.join(os.path.dirname(target), f"{stem} [REVERTED].z64")
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Write the recovered original as", suggested, "N64 ROMs (*.z64);;All files (*)"
        )
        if not out_path:
            return
        ok, message = manifest_mod.revert(target, man, out_path)
        if ok:
            self.log(f"✅ {message}")
            QMessageBox.information(self, "Reverted", message)
        else:
            self.log(f"❌ Revert refused: {message}")
            QMessageBox.critical(self, "Revert refused", message)

    def clear_list(self) -> None:
        self.rom_list.clear()
        self.rom_list_widget.clear()
        for temp_dir in self.temp_dirs:
            cleanup_temp_dir(temp_dir)
        self.temp_dirs.clear()
        self.update_status_bar()
        self.update_hires_availability()
        self.log("🗑️ List cleared")

    # ------------------------------------------------------- Helpers

    def _hires_supported_names(self, _roms: list[str] | None = None) -> list[str]:
        """Basenames of loaded ROMs that have a verified 640x480 patch.

        Reads only the cache filled by HiresScanWorker - the check reads a
        megabyte per ROM, so it must never run on the UI thread. The
        argument is accepted (and ignored) because older call sites passed
        a ROM list; the loaded list is the only one that matters.
        """
        names = []
        for rom in self.rom_list:
            if self._hires_cache.get(rom):
                names.append(os.path.basename(rom))
        return names

    def _any_hires_supported(self) -> bool:
        return bool(self._hires_supported_names())

    def _hires_scan_pending(self) -> bool:
        if self._hires_scan_worker is not None and self._hires_scan_worker.isRunning():
            return True
        return any(rom not in self._hires_cache for rom in self.rom_list)

    def _scan_hires_inline(self, roms: list[str]) -> None:
        """Fill the cache synchronously - test hook and fallback, not the
        normal path (see update_hires_availability)."""
        for rom in roms:
            try:
                info = core.inspect_rom_details(rom)
                supported = info.get("hires_support") == core.HIRES_VERIFIED
            except Exception:
                supported = False
            self._hires_cache[rom] = supported

    def update_hires_availability(self, sync: bool = False) -> None:
        """Enable the 640x480 checkbox only when a loaded ROM can take it.

        The generic VI-table widening renders incorrectly on hardware, so
        offering it for arbitrary ROMs produced broken output. Verified
        dumps and ROMs that are already hi-res are the only cases where the
        box does anything useful.

        The support scan runs on a background thread; this applies whatever
        the cache knows and starts a scan for the rest. Pass sync=True to
        scan inline first - that is for tests, which must observe the final
        state without an event loop.
        """
        uncached = [rom for rom in self.rom_list if rom not in self._hires_cache]
        if uncached and sync:
            self._scan_hires_inline(uncached)
            uncached = []

        if uncached and self._hires_scan_worker is None:
            self._hires_scan_worker = HiresScanWorker(uncached)
            self._hires_scan_worker.done.connect(self._on_hires_scan_done)
            self._hires_scan_worker.start()
        # A scan already running? Its done-handler re-runs this method,
        # which picks up whatever was added meanwhile.

        if not self.rom_list:
            self.cb_hires.setEnabled(True)
            self.cb_hires.setToolTip("Load ROMs to see whether 640x480 is available for them.")
            return

        supported = self._hires_supported_names()
        pending = (
            len(self.rom_list)
            - len(supported)
            - sum(
                1
                for rom in self.rom_list
                if rom in self._hires_cache and not self._hires_cache[rom]
            )
        )

        if supported:
            self.cb_hires.setEnabled(True)
            shown = "\n".join(f"  • {n}" for n in supported[:5])
            more = f"\n  … and {len(supported) - 5} more" if len(supported) > 5 else ""
            checking = f"\n\n(checking {pending} recently added ROM(s)…)" if pending else ""
            self.cb_hires.setToolTip(
                f"Verified 640x480 patch available for {len(supported)} of "
                f"{len(self.rom_list)} ROM(s):\n{shown}{more}{checking}\n\n"
                "ROMs without a verified patch are skipped, not broken."
            )
            self.cb_hires.setText(
                f"High-Res 640x480 — verified for {len(supported)} of {len(self.rom_list)} ROM(s)"
            )
        elif pending:
            # Nothing verified yet, but not everything has been scanned:
            # stay neutral rather than declaring the feature unavailable.
            self.cb_hires.setEnabled(False)
            self.cb_hires.setText("High-Res 640x480 — checking loaded ROMs…")
            self.cb_hires.setToolTip(
                "Reading the loaded ROMs to see whether any has a verified "
                "640x480 patch. The box becomes available the moment one "
                "does."
            )
        else:
            self.cb_hires.setChecked(False)
            self.cb_hires.setEnabled(False)
            self.cb_hires.setToolTip(
                "No loaded ROM has a verified 640x480 patch.\n\n"
                "Widening the VI tables alone leaves the framebuffer and RDP "
                "scaling at 320, which renders incorrectly on real hardware: "
                "doubled image, menus and UI in the wrong place.\n\n"
                "Verified patches exist for 8 dumps (Super Mario 64, GoldenEye, "
                "Banjo-Kazooie Rev A, F-Zero X, Forsaken 64, Pokemon Snap, "
                "Quake II, Golden Nugget 64)."
            )
            self.cb_hires.setText("High-Res 640x480 — not available for these ROMs")

    def _on_hires_scan_done(self, results: list) -> None:
        self._hires_cache.update(results)
        worker, self._hires_scan_worker = self._hires_scan_worker, None
        if worker is not None:
            worker.wait(2000)
        self.update_hires_availability()

    # ---------------------------------------------------- Saves tab

    def _build_save_tab(self) -> QWidget:
        """Moving a save between an emulator and a flashcart.

        Both ends are chosen by the user rather than detected, for the
        same reason the engine refuses to detect them: measured against a
        132-save card, automatic detection was wrong on one save in five,
        and a wrong answer scrambles every byte of somebody's progress.
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(
            QLabel(
                "Emulators and flashcarts disagree about how to lay a save chip "
                "out in a file. Name where the file came from and where it is "
                "going; the byte order is looked up, never guessed."
            )
        )

        picker = QHBoxLayout()
        picker.addWidget(QLabel("From:"))
        self.save_from = QComboBox()
        picker.addWidget(self.save_from)
        picker.addWidget(QLabel("To:"))
        self.save_to = QComboBox()
        picker.addWidget(self.save_to)
        for combo in (self.save_from, self.save_to):
            for src in savegame.SOURCES:
                combo.addItem(src.label, src.key)
                combo.setItemData(combo.count() - 1, src.evidence, Qt.ItemDataRole.ToolTipRole)
        # A conversion between two different tools is the normal case.
        self.save_to.setCurrentIndex(min(1, self.save_to.count() - 1))
        picker.addStretch(1)
        layout.addLayout(picker)

        self.save_list_widget = QListWidget()
        self.save_list_widget.setToolTip("Save files to convert. Drag them in, or use Add.")
        layout.addWidget(self.save_list_widget)

        buttons = QHBoxLayout()
        btn_add = QPushButton("➕ Add saves")
        btn_add.clicked.connect(self.add_save_files)
        btn_clear = QPushButton("🗑️ Clear")
        btn_clear.clicked.connect(self.clear_saves)
        self.btn_save_info = QPushButton("🔍 Inspect")
        self.btn_save_info.clicked.connect(self.inspect_saves)
        self.btn_save_convert = QPushButton("💾 Convert")
        self.btn_save_convert.clicked.connect(self.convert_saves)
        buttons.addWidget(btn_add)
        buttons.addWidget(btn_clear)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_save_info)
        buttons.addWidget(self.btn_save_convert)
        layout.addLayout(buttons)

        self.save_output = QPlainTextEdit()
        self.save_output.setReadOnly(True)
        self.save_output.setFont(QFont("Menlo", 9))
        layout.addWidget(self.save_output)

        return tab

    def _save_log(self, message: object = "") -> None:
        self.save_output.appendPlainText(str(message))

    def add_save_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select save files",
            "",
            "N64 saves (*.sav *.eep *.sra *.srm *.fla *.mpk);;All files (*)",
        )
        self.add_saves(files)

    def add_saves(self, paths: list[str]) -> int:
        added = 0
        for path in savegame.collect_saves(list(paths)):
            if path not in self.save_list:
                self.save_list.append(path)
                self.save_list_widget.addItem(os.path.basename(path))
                added += 1
        return added

    def clear_saves(self) -> None:
        self.save_list = []
        self.save_list_widget.clear()
        self.save_output.clear()

    def inspect_saves(self) -> None:
        if not self._require_saves():
            return
        self.save_output.clear()
        self._set_save_busy(True)
        self.save_worker = SaveBatchWorker("inspect", self.save_list)
        self.save_worker.line.connect(self._save_log)
        self.save_worker.done.connect(self._on_save_job_done)
        self.save_worker.start()

    def convert_saves(self) -> None:
        if not self._require_saves():
            return
        source = self.save_from.currentData()
        target = self.save_to.currentData()
        out_dir = QFileDialog.getExistingDirectory(self, "Where should the converted saves go?")
        if not out_dir:
            return

        self.save_output.clear()
        self._set_save_busy(True)
        self.save_worker = SaveBatchWorker(
            "convert", self.save_list, source=source, target=target, out_dir=out_dir
        )
        self.save_worker.line.connect(self._save_log)
        self.save_worker.done.connect(self._on_save_job_done)
        self.save_worker.start()

    def _on_save_job_done(self, results: dict) -> None:
        worker, self.save_worker = self.save_worker, None
        if worker is not None:
            worker.wait(2000)
        if results.get("job") == "inspect":
            self._set_save_busy(False)
            return

        converted = results.get("converted", 0)
        skipped = failed = results.get("failed", 0)
        # A conversion that would overwrite an existing save was held back
        # by the worker. Ask about each one here on the UI thread - a save
        # cannot be recovered once it is overwritten.
        for path, existing in results["conflicts"]:
            name = os.path.basename(path)
            if self._confirm_replace(existing):
                again = savegame.convert_file(
                    path,
                    self.save_from.currentData(),
                    self.save_to.currentData(),
                    out_dir=os.path.dirname(existing),
                    force=True,
                )
                if again["status"] == "converted":
                    converted += 1
                    self._save_log(f"✅ {name}  (replaced)")
                else:
                    failed += 1
                    self._save_log(f"❌ {name}: {again['message']}")
            else:
                skipped += 1
                self._save_log(f"⏭️  {name}: kept the existing file")

        self._set_save_busy(False)
        self._save_log(f"\n{converted} converted, {skipped} skipped, {failed} failed")
        self.log(f"Saves: {converted} converted, {skipped} skipped, {failed} failed")

    def _set_save_busy(self, busy: bool) -> None:
        self.btn_save_info.setEnabled(not busy)
        self.btn_save_convert.setEnabled(not busy)

    def _confirm_replace(self, path: str) -> bool:
        answer = QMessageBox.question(
            self,
            "Replace this save?",
            f"{os.path.basename(path)} already exists.\n\n"
            f"A save cannot be recovered once it is overwritten. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _require_saves(self) -> bool:
        if self.save_list:
            return True
        QMessageBox.warning(self, "No saves", "Add some save files first.")
        return False

    def log(self, message: object) -> None:
        self.log_widget.appendPlainText(str(message))

    def update_status_bar(self) -> None:
        tools = core.check_tools()
        parts = []
        for name, label in (("u64aap", "u64aap"), ("rn64crc", "rn64crc")):
            parts.append(f"{label}: {'✓' if tools.get(name) else '✗ (Fallback)'}")
        # xdelta3 is no longer a capability question - the built-in VCDIFF
        # engine covers the same deltas - so the label names the engine in
        # use rather than flagging a missing tool.
        parts.append("xdelta: " + ("xdelta3 ✓" if tools.get("xdelta3") else "Pure-Python ✓"))
        parts.append("CRC-Engine: Pure-Python ✓")
        self.status_tool_label.setText("  |  ".join(parts))
        self.status_count_label.setText(f"{len(self.rom_list)} ROM(s) loaded")

    # ---------------------------------------------------- Inspection

    def start_inspection(self) -> None:
        if not self.rom_list:
            QMessageBox.warning(self, "No ROMs", "Add some ROMs first.")
            return

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self.last_infos = []
        self.btn_inspect.setEnabled(False)
        self.btn_export_csv.setEnabled(False)
        self.btn_export_json.setEnabled(False)
        self.act_inspect.setEnabled(False)
        self.inspect_progress.setVisible(True)
        self.inspect_progress.setMaximum(len(self.rom_list))
        self.inspect_progress.setValue(0)

        dat_index = None
        if self.cb_dats.isChecked():
            dat_index = self._load_dat_index()

        self.log("\n🔍 Inspecting ROMs in the background...")
        # A DAT match needs the ROM's hashes, so asking for one implies
        # the (slower) hashing pass.
        with_hashes = self.cb_hashes.isChecked() or dat_index is not None
        self.inspect_worker = InspectWorker(self.rom_list, with_hashes=with_hashes, dat=dat_index)
        self.inspect_worker.item_ready.connect(self.on_inspect_item)
        self.inspect_worker.done.connect(self.on_inspection_done)
        self.inspect_worker.start()

    def on_inspect_item(self, info: dict) -> None:
        self.last_infos.append(info)
        res = "640x480" if info.get("is_hires_640x480") else "320x240"
        aa = "No-AA" if info.get("no_aa") else "AA"
        item = QTreeWidgetItem(
            [
                info.get("filename", ""),
                info.get("title", ""),
                info.get("region", ""),
                info.get("format", ""),
                str(info.get("size_mb", "")),
                res,
                aa,
                str(info.get("vi_table_count", 0)),
                {
                    core.HIRES_VERIFIED: "verified",
                    core.HIRES_NATIVE: "native",
                    core.HIRES_UNSUPPORTED: "unsupported",
                }.get(info.get("hires_support") or "", ""),
                info.get("crc1", ""),
                info.get("crc2", ""),
                "✓" if info.get("has_subdrag_patch") else "",
                info.get("md5", ""),
                info.get("sha1", ""),
            ]
        )
        self.tree.addTopLevelItem(item)
        self.inspect_progress.setValue(len(self.last_infos))
        self.log(
            f"{info.get('filename', '')}: {info.get('title', '')} "
            f"[{info.get('region', '')}] {res} | {aa}"
        )

    def on_inspection_done(self, infos: list) -> None:
        worker, self.inspect_worker = self.inspect_worker, None
        if worker is not None:
            worker.wait(2000)
        self.btn_inspect.setEnabled(True)
        self.act_inspect.setEnabled(True)
        self.btn_export_csv.setEnabled(bool(infos))
        self.btn_export_json.setEnabled(bool(infos))
        self.inspect_progress.setVisible(False)
        self.tree.setSortingEnabled(True)
        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)
        self.log(f"\n✅ Inspection complete ({len(infos)} ROM(s))")

    def export_report(self, fmt: str) -> None:
        if not self.last_infos:
            return
        default_name = f"n64_report.{fmt}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report", default_name, "CSV (*.csv)" if fmt == "csv" else "JSON (*.json)"
        )
        if not path:
            return
        if fmt == "csv" and not path.lower().endswith(".csv"):
            path += ".csv"
        if fmt == "json" and not path.lower().endswith(".json"):
            path += ".json"
        try:
            core.export_report(self.last_infos, path)
            self.log(f"💾 Report written: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    # ----------------------------------------------------- Patching

    def start_patching(self) -> None:
        if not self.rom_list:
            QMessageBox.warning(self, "No ROMs", "Add some ROMs first.")
            return

        options = core.PatchOptions(
            no_aa=self.cb_no_aa.isChecked(),
            no_dither=self.cb_no_dither.isChecked(),
            no_divot=self.cb_no_divot.isChecked(),
            no_gamma=self.cb_no_gamma.isChecked(),
            hires=self.cb_hires.isChecked(),
            write_manifest=self.cb_manifest.isChecked(),
        )

        self.btn_patch.setEnabled(False)
        self.btn_inspect.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.act_start.setEnabled(False)
        self.act_inspect.setEnabled(False)
        self.act_cancel.setEnabled(True)
        self._set_led("busy")
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.rom_list))
        self.progress_bar.setValue(0)

        output_dir = self._patch_output_dir()
        if output_dir:
            self.log(f"📤 Output directory: {output_dir}")
        self.log(f"\n🚀 Patching {len(self.rom_list)} ROM(s)...")

        self.worker = PatchWorker(
            self.rom_list,
            options,
            strip_header=self.cb_strip_header.isChecked(),
            fix_crc=self.cb_fix_crc.isChecked(),
            output_dir=output_dir,
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.done.connect(self.on_finished)
        self.worker.log_message.connect(self.log)
        self.worker.start()

    def update_progress(self, current: int, total: int, filename: str) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"({current}/{total}) {filename}")

    def cancel_patching(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.log("⛔ Cancellation requested...")

    def on_finished(self, results: dict) -> None:
        self.btn_patch.setEnabled(True)
        self.btn_inspect.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.act_start.setEnabled(True)
        self.act_inspect.setEnabled(True)
        self.act_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("")
        self._set_led("error" if results.get("errors") else "ok")

        self.log(f"\n{'=' * 60}")
        self.log(
            f"✅ Done. Patched: {results['patched']}, "
            f"Skipped: {results['skipped']}, Errors: {results['errors']}"
        )
        self.log(f"{'=' * 60}\n")

        # Append the run's lines to the persistent log file.
        if self.worker is not None and getattr(self.worker, "log_lines", None):
            try:
                core.append_log(self.worker.log_lines)
            except OSError:
                pass

        QMessageBox.information(
            self,
            "Patching complete",
            f"Patched: {results['patched']}\n"
            f"Skipped: {results['skipped']}\n"
            f"Errors: {results['errors']}",
        )

        for temp_dir in self.temp_dirs:
            cleanup_temp_dir(temp_dir)
        self.temp_dirs.clear()

    # ----------------------------------------------------- Settings

    def load_settings(self) -> None:
        self.cb_no_aa.setChecked(self.settings.value("no_aa", True, type=bool))
        self.cb_no_dither.setChecked(self.settings.value("no_dither", True, type=bool))
        self.cb_no_divot.setChecked(self.settings.value("no_divot", False, type=bool))
        self.cb_no_gamma.setChecked(self.settings.value("no_gamma", False, type=bool))
        self.cb_hires.setChecked(self.settings.value("hires", False, type=bool))
        self.cb_strip_header.setChecked(self.settings.value("strip_header", False, type=bool))
        self.cb_fix_crc.setChecked(self.settings.value("fix_crc", False, type=bool))
        self.cb_manifest.setChecked(self.settings.value("write_manifest", False, type=bool))
        self.cb_dats.setChecked(self.settings.value("use_dats", True, type=bool))
        output_dir = self.settings.value("output_dir", "", type=str)
        if output_dir and os.path.isdir(output_dir):
            self.output_dir_edit.setText(output_dir)
        preset_index = self.settings.value("preset_index", 0, type=int)
        if 0 <= preset_index < self.preset_combo.count():
            self.preset_combo.setCurrentIndex(preset_index)

    def save_settings(self) -> None:
        self.settings.setValue("no_aa", self.cb_no_aa.isChecked())
        self.settings.setValue("no_dither", self.cb_no_dither.isChecked())
        self.settings.setValue("no_divot", self.cb_no_divot.isChecked())
        self.settings.setValue("no_gamma", self.cb_no_gamma.isChecked())
        self.settings.setValue("hires", self.cb_hires.isChecked())
        self.settings.setValue("strip_header", self.cb_strip_header.isChecked())
        self.settings.setValue("fix_crc", self.cb_fix_crc.isChecked())
        self.settings.setValue("write_manifest", self.cb_manifest.isChecked())
        self.settings.setValue("use_dats", self.cb_dats.isChecked())
        self.settings.setValue("preset_index", self.preset_combo.currentIndex())
        self.settings.setValue("output_dir", self.output_dir_edit.text().strip())

    def closeEvent(self, event: QCloseEvent | None) -> None:
        # Clean shutdown: stop running workers, clean up temp directories,
        # persist settings.
        self.save_settings()
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        if self.inspect_worker is not None and self.inspect_worker.isRunning():
            self.inspect_worker.wait(5000)
        if self._hires_scan_worker is not None and self._hires_scan_worker.isRunning():
            self._hires_scan_worker.wait(5000)
        if self.save_worker is not None and self.save_worker.isRunning():
            self.save_worker.wait(5000)
        for temp_dir in self.temp_dirs:
            cleanup_temp_dir(temp_dir)
        self.temp_dirs.clear()
        if event is not None:
            event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    app.setStyleSheet(theme.stylesheet())

    window = N64PatcherGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
