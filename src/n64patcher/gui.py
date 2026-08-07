"""
n64patcher.gui - PyQt6 desktop interface
Universal N64 ROM Inspector & Smart Patcher - PyQt6 GUI

Design notes:
  - PatchWorker emits 'done' rather than shadowing QThread's own
    'finished' signal; the worker is stopped cleanly on close.
  - Inspection runs on a background thread and fills a QTreeWidget table
    (title, region, CRC1/CRC2, hashes, ...).
  - Drag & drop accepts files, folders and archives.
  - The status bar shows tool availability; log lines are additionally
    written to the persistent log file (core.append_log).
  - Extraction temp directories live in the system temp area and are
    cleaned up on close even when no patch run happened.
"""
import os
import sys
from datetime import datetime

from PyQt6.QtCore import QSettings, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
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

    def __init__(self, roms, options, strip_header=False, fix_crc=False):
        super().__init__()
        self.roms = roms
        self.options = options
        self.strip_header = strip_header
        self.fix_crc = fix_crc
        self.should_cancel = False
        self.log_lines = []

    def cancel(self):
        self.should_cancel = True

    def _log(self, msg):
        self.log_lines.append(str(msg))
        self.log_message.emit(str(msg))

    def run(self):
        results = {"patched": 0, "skipped": 0, "errors": 0, "details": []}
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
                    should_cancel=lambda: self.should_cancel
                )

                if not isinstance(result, dict):
                    result = {"status": "error", "message": "Invalid patch result", "output": None}

                out_file = result.get("output")

                # Optional (idempotent) CRC pass for flashcarts
                if self.fix_crc and result.get("status") == "patched" \
                        and out_file and os.path.isfile(out_file):
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

    def __init__(self, roms, with_hashes=True):
        super().__init__()
        self.roms = roms
        self.with_hashes = with_hashes

    def run(self):
        infos = []
        for rom in self.roms:
            try:
                info = core.inspect_rom_details(rom, with_hashes=self.with_hashes)
            except Exception as e:
                info = {
                    "filename": os.path.basename(rom), "path": rom,
                    "format": f"Error: {e}", "title": "", "region": "",
                    "size_mb": 0, "no_aa": False, "is_hires_640x480": False,
                    "vi_table_count": 0, "crc1": "", "crc2": "",
                    "has_subdrag_patch": False,
                }
            infos.append(info)
            self.item_ready.emit(info)
        self.done.emit(infos)


class N64PatcherGUI(QMainWindow):
    def __init__(self):
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
        self.rom_list = []
        self.temp_dirs = []
        self.worker = None
        self.inspect_worker = None
        self.last_infos = []

        self.init_ui()
        self.load_settings()
        self.update_status_bar()

    # ------------------------------------------------------------------ UI

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # Tab 1: Patching
        patch_tab = QWidget()
        patch_layout = QVBoxLayout(patch_tab)
        tabs.addTab(patch_tab, "🎮 Patching")

        preset_group = QGroupBox("📋 Choose a preset profile")
        preset_layout = QVBoxLayout()

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("⚙️ Custom (individual settings)", "custom")
        for preset in list_presets():
            self.preset_combo.addItem(f"{preset['name']} - {preset['description']}", preset['key'])
        self.preset_combo.currentIndexChanged.connect(self.on_preset_changed)
        preset_layout.addWidget(self.preset_combo)

        self.preset_warning_label = QLabel("")
        self.preset_warning_label.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        self.preset_warning_label.setWordWrap(True)
        preset_layout.addWidget(self.preset_warning_label)

        preset_group.setLayout(preset_layout)
        patch_layout.addWidget(preset_group)

        options_group = QGroupBox("🎨 Visual filters (individual settings)")
        options_layout = QVBoxLayout()
        self.cb_no_aa = QCheckBox("Remove anti-aliasing (No-AA) - sharper edges")
        self.cb_no_dither = QCheckBox("Remove dither filter - no 16-bit artifacts")
        self.cb_no_divot = QCheckBox("Remove divot filter - no edge blurring")
        self.cb_no_gamma = QCheckBox("Remove gamma boost - accurate colors")
        self.cb_hires = QCheckBox("High-Res 640x480 (Smart VI Table Engine)")
        for cb in [self.cb_no_aa, self.cb_no_dither, self.cb_no_divot,
                   self.cb_no_gamma, self.cb_hires]:
            options_layout.addWidget(cb)
        options_group.setLayout(options_layout)
        patch_layout.addWidget(options_group)

        flashcart_group = QGroupBox("💾 Flashcart options")
        flashcart_layout = QVBoxLayout()
        self.cb_strip_header = QCheckBox("Strip scene header (iN0000 etc.)")
        self.cb_fix_crc = QCheckBox("Repair CRC1/CRC2 (EverDrive compatible)")
        flashcart_layout.addWidget(self.cb_strip_header)
        flashcart_layout.addWidget(self.cb_fix_crc)
        flashcart_group.setLayout(flashcart_layout)
        patch_layout.addWidget(flashcart_group)

        # No bare "&" in a QGroupBox title: Qt reads it as a mnemonic marker
        # and renders "drag & drop" as "drag _drop".
        list_group = QGroupBox("📁 ROM library (drag and drop supported)")
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
        self.btn_patch = QPushButton("🚀 Start patching")
        self.btn_cancel = QPushButton("⛔ Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_inspect.clicked.connect(self.start_inspection)
        self.btn_patch.clicked.connect(self.start_patching)
        self.btn_cancel.clicked.connect(self.cancel_patching)
        action_layout.addWidget(self.btn_inspect)
        action_layout.addWidget(self.btn_patch)
        action_layout.addWidget(self.btn_cancel)
        patch_layout.addLayout(action_layout)

        # Tab 2: Inspector
        inspect_tab = QWidget()
        inspect_layout = QVBoxLayout(inspect_tab)
        tabs.addTab(inspect_tab, "🔍 Inspector")

        inspect_ctrl = QHBoxLayout()
        self.cb_hashes = QCheckBox("Compute MD5/SHA-1 (slow)")
        self.cb_hashes.setChecked(True)
        self.btn_export_csv = QPushButton("💾 Export CSV")
        self.btn_export_json = QPushButton("💾 Export JSON")
        self.btn_export_csv.clicked.connect(lambda: self.export_report("csv"))
        self.btn_export_json.clicked.connect(lambda: self.export_report("json"))
        self.btn_export_csv.setEnabled(False)
        self.btn_export_json.setEnabled(False)
        inspect_ctrl.addWidget(self.cb_hashes)
        inspect_ctrl.addStretch(1)
        inspect_ctrl.addWidget(self.btn_export_csv)
        inspect_ctrl.addWidget(self.btn_export_json)
        inspect_layout.addLayout(inspect_ctrl)

        self.inspect_progress = QProgressBar()
        self.inspect_progress.setVisible(False)
        inspect_layout.addWidget(self.inspect_progress)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(13)
        self.tree.setHeaderLabels([
            "File", "Title", "Region", "Format", "Size (MB)", "Resolution",
            "AA", "VI tables", "640x480", "CRC1", "CRC2", "SubDrag patch",
            "MD5", "SHA1"
        ])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        inspect_layout.addWidget(self.tree)

        # Tab 3: Log
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        tabs.addTab(log_tab, "📜 Log")

        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumBlockCount(10000)
        self.log_widget.setFont(QFont("Menlo", 9))
        log_layout.addWidget(self.log_widget)

        # Statusleiste
        self.status_bar = self.statusBar()
        self.status_tool_label = QLabel("")
        self.status_count_label = QLabel("")
        self.status_bar.addWidget(self.status_tool_label)
        self.status_bar.addPermanentWidget(self.status_count_label)

        self.log(f"🎮 N64 Smart Patcher v{core.VERSION} started")
        self.log(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log(f"📂 Log file: {core.get_log_path()}")

    # ------------------------------------------------------- Presets

    def on_preset_changed(self, index):
        preset_key = self.preset_combo.currentData()

        if preset_key == "custom":
            self.preset_warning_label.setText("")
            for cb in [self.cb_no_aa, self.cb_no_dither, self.cb_no_divot,
                       self.cb_no_gamma, self.cb_hires]:
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

            for cb in [self.cb_no_aa, self.cb_no_dither, self.cb_no_divot,
                       self.cb_no_gamma, self.cb_hires]:
                cb.setEnabled(False)

            warnings = list(get_preset_warnings(preset_key))
            # A preset asking for hi-res on ROMs that cannot take it would
            # otherwise show a ticked box that quietly does nothing.
            if options.hires and self.rom_list and not self._any_hires_supported():
                warnings.append(
                    "640x480 will be skipped: none of the loaded ROMs has a "
                    "verified patch (widening alone breaks rendering).")
            if warnings:
                self.preset_warning_label.setText("\n".join(f"⚠️ {w}" for w in warnings))
            else:
                self.preset_warning_label.setText("")

    # -------------------------------------------------- Drag & Drop

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.isLocalFile()]
        self.add_paths(paths)

    # ------------------------------------------------- ROM-Verwaltung

    def add_paths(self, paths):
        for path in paths:
            try:
                if os.path.isdir(path):
                    self._add_folder_contents(path)
                elif is_archive(path):
                    self._add_archive(path)
                elif core.is_rom_file(path) and not core.is_tool_output(path):
                    self._add_rom(path)
            except Exception as e:
                self.log(f"⚠️ Error adding {path}: {e}")
        self.update_status_bar()
        self.update_hires_availability()

    def _add_rom(self, path):
        if path not in self.rom_list:
            self.rom_list.append(path)
            self.rom_list_widget.addItem(os.path.basename(path))

    def _add_archive(self, path):
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

    def _add_folder_contents(self, folder):
        for root, _, files in os.walk(folder):
            for file in files:
                full_path = os.path.join(root, file)
                try:
                    if is_archive(full_path):
                        self._add_archive(full_path)
                    elif core.is_rom_file(file) and not core.is_tool_output(full_path):
                        self._add_rom(full_path)
                except Exception as e:
                    self.log(f"⚠️ Error on file {file}: {e}")

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select ROM files", "",
            "N64 ROMs & archives (*.z64 *.v64 *.n64 *.zip *.7z);;All files (*)"
        )
        self.add_paths(files)
        self.log(f"📊 {len(self.rom_list)} ROM(s) in the list")

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            self.add_paths([folder])
            self.log(f"📊 {len(self.rom_list)} ROM(s) in the list")

    def clear_list(self):
        self.rom_list.clear()
        self.rom_list_widget.clear()
        for temp_dir in self.temp_dirs:
            cleanup_temp_dir(temp_dir)
        self.temp_dirs.clear()
        self.update_status_bar()
        self.update_hires_availability()
        self.log("🗑️ List cleared")

    # ------------------------------------------------------- Helpers

    def _hires_supported_names(self):
        """Basenames of loaded ROMs that have a verified 640x480 patch."""
        names = []
        for rom in self.rom_list:
            try:
                info = core.inspect_rom_details(rom)
            except Exception:
                continue
            if info.get("hires_support") == core.HIRES_VERIFIED:
                names.append(os.path.basename(rom))
        return names

    def _any_hires_supported(self):
        return bool(self._hires_supported_names())

    def update_hires_availability(self):
        """Enable the 640x480 checkbox only when a loaded ROM can take it.

        The generic VI-table widening renders incorrectly on hardware, so
        offering it for arbitrary ROMs produced broken output. Verified
        dumps and ROMs that are already hi-res are the only cases where the
        box does anything useful.
        """
        if not self.rom_list:
            self.cb_hires.setEnabled(True)
            self.cb_hires.setToolTip(
                "Load ROMs to see whether 640x480 is available for them.")
            return

        supported = self._hires_supported_names()

        if supported:
            self.cb_hires.setEnabled(True)
            shown = "\n".join(f"  • {n}" for n in supported[:5])
            more = (f"\n  … and {len(supported) - 5} more"
                    if len(supported) > 5 else "")
            self.cb_hires.setToolTip(
                f"Verified 640x480 patch available for {len(supported)} of "
                f"{len(self.rom_list)} ROM(s):\n{shown}{more}\n\n"
                "ROMs without a verified patch are skipped, not broken.")
            self.cb_hires.setText(
                f"High-Res 640x480 — verified for {len(supported)} "
                f"of {len(self.rom_list)} ROM(s)")
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
                "Quake II, Golden Nugget 64).")
            self.cb_hires.setText(
                "High-Res 640x480 — not available for these ROMs")

    def log(self, message):
        self.log_widget.appendPlainText(str(message))

    def update_status_bar(self):
        tools = core.check_tools()
        parts = []
        for name, label in (("u64aap", "u64aap"), ("rn64crc", "rn64crc"),
                            ("xdelta3", "xdelta3")):
            parts.append(f"{label}: {'✓' if tools.get(name) else '✗ (Fallback)'}")
        parts.append("CRC-Engine: Pure-Python ✓")
        self.status_tool_label.setText("  |  ".join(parts))
        self.status_count_label.setText(f"{len(self.rom_list)} ROM(s) loaded")

    # ---------------------------------------------------- Inspection

    def start_inspection(self):
        if not self.rom_list:
            QMessageBox.warning(self, "No ROMs", "Add some ROMs first.")
            return

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self.last_infos = []
        self.btn_inspect.setEnabled(False)
        self.btn_export_csv.setEnabled(False)
        self.btn_export_json.setEnabled(False)
        self.inspect_progress.setVisible(True)
        self.inspect_progress.setMaximum(len(self.rom_list))
        self.inspect_progress.setValue(0)

        self.log("\n🔍 Inspecting ROMs in the background...")
        self.inspect_worker = InspectWorker(self.rom_list,
                                            with_hashes=self.cb_hashes.isChecked())
        self.inspect_worker.item_ready.connect(self.on_inspect_item)
        self.inspect_worker.done.connect(self.on_inspection_done)
        self.inspect_worker.start()

    def on_inspect_item(self, info):
        self.last_infos.append(info)
        res = "640x480" if info.get("is_hires_640x480") else "320x240"
        aa = "No-AA" if info.get("no_aa") else "AA"
        item = QTreeWidgetItem([
            info.get("filename", ""),
            info.get("title", ""),
            info.get("region", ""),
            info.get("format", ""),
            str(info.get("size_mb", "")),
            res,
            aa,
            str(info.get("vi_table_count", 0)),
            {core.HIRES_VERIFIED: "verified",
             core.HIRES_NATIVE: "native",
             core.HIRES_UNSUPPORTED: "unsupported"}.get(
                info.get("hires_support"), ""),
            info.get("crc1", ""),
            info.get("crc2", ""),
            "✓" if info.get("has_subdrag_patch") else "",
            info.get("md5", ""),
            info.get("sha1", ""),
        ])
        self.tree.addTopLevelItem(item)
        self.inspect_progress.setValue(len(self.last_infos))
        self.log(f"{info.get('filename', '')}: {info.get('title', '')} "
                 f"[{info.get('region', '')}] {res} | {aa}")

    def on_inspection_done(self, infos):
        self.btn_inspect.setEnabled(True)
        self.btn_export_csv.setEnabled(bool(infos))
        self.btn_export_json.setEnabled(bool(infos))
        self.inspect_progress.setVisible(False)
        self.tree.setSortingEnabled(True)
        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)
        self.log(f"\n✅ Inspection complete ({len(infos)} ROM(s))")

    def export_report(self, fmt):
        if not self.last_infos:
            return
        default_name = f"n64_report.{fmt}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report", default_name,
            "CSV (*.csv)" if fmt == "csv" else "JSON (*.json)")
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

    def start_patching(self):
        if not self.rom_list:
            QMessageBox.warning(self, "No ROMs", "Add some ROMs first.")
            return

        options = core.PatchOptions(
            no_aa=self.cb_no_aa.isChecked(),
            no_dither=self.cb_no_dither.isChecked(),
            no_divot=self.cb_no_divot.isChecked(),
            no_gamma=self.cb_no_gamma.isChecked(),
            hires=self.cb_hires.isChecked(),
        )

        self.btn_patch.setEnabled(False)
        self.btn_inspect.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.rom_list))
        self.progress_bar.setValue(0)

        self.log(f"\n🚀 Patching {len(self.rom_list)} ROM(s)...")

        self.worker = PatchWorker(
            self.rom_list,
            options,
            strip_header=self.cb_strip_header.isChecked(),
            fix_crc=self.cb_fix_crc.isChecked()
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.done.connect(self.on_finished)
        self.worker.log_message.connect(self.log)
        self.worker.start()

    def update_progress(self, current, total, filename):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"({current}/{total}) {filename}")

    def cancel_patching(self):
        if self.worker:
            self.worker.cancel()
            self.log("⛔ Cancellation requested...")

    def on_finished(self, results):
        self.btn_patch.setEnabled(True)
        self.btn_inspect.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("")

        self.log(f"\n{'='*60}")
        self.log(f"✅ Done. Patched: {results['patched']}, "
                 f"Skipped: {results['skipped']}, Errors: {results['errors']}")
        self.log(f"{'='*60}\n")

        # Persistente Logdatei beschreiben
        if self.worker is not None and getattr(self.worker, "log_lines", None):
            try:
                core.append_log(self.worker.log_lines)
            except OSError:
                pass

        QMessageBox.information(
            self, "Patching complete",
            f"Patched: {results['patched']}\n"
            f"Skipped: {results['skipped']}\n"
            f"Errors: {results['errors']}"
        )

        for temp_dir in self.temp_dirs:
            cleanup_temp_dir(temp_dir)
        self.temp_dirs.clear()

    # ----------------------------------------------------- Settings

    def load_settings(self):
        self.cb_no_aa.setChecked(self.settings.value("no_aa", True, type=bool))
        self.cb_no_dither.setChecked(self.settings.value("no_dither", True, type=bool))
        self.cb_no_divot.setChecked(self.settings.value("no_divot", False, type=bool))
        self.cb_no_gamma.setChecked(self.settings.value("no_gamma", False, type=bool))
        self.cb_hires.setChecked(self.settings.value("hires", False, type=bool))
        self.cb_strip_header.setChecked(self.settings.value("strip_header", False, type=bool))
        self.cb_fix_crc.setChecked(self.settings.value("fix_crc", False, type=bool))
        preset_index = self.settings.value("preset_index", 0, type=int)
        if 0 <= preset_index < self.preset_combo.count():
            self.preset_combo.setCurrentIndex(preset_index)

    def save_settings(self):
        self.settings.setValue("no_aa", self.cb_no_aa.isChecked())
        self.settings.setValue("no_dither", self.cb_no_dither.isChecked())
        self.settings.setValue("no_divot", self.cb_no_divot.isChecked())
        self.settings.setValue("no_gamma", self.cb_no_gamma.isChecked())
        self.settings.setValue("hires", self.cb_hires.isChecked())
        self.settings.setValue("strip_header", self.cb_strip_header.isChecked())
        self.settings.setValue("fix_crc", self.cb_fix_crc.isChecked())
        self.settings.setValue("preset_index", self.preset_combo.currentIndex())

    def closeEvent(self, event):
        # Sauberer Shutdown: laufende Worker stoppen, Temp-Verzeichnisse
        # clean up, persist settings.
        self.save_settings()
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        if self.inspect_worker is not None and self.inspect_worker.isRunning():
            self.inspect_worker.wait(5000)
        for temp_dir in self.temp_dirs:
            cleanup_temp_dir(temp_dir)
        self.temp_dirs.clear()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    app.setStyleSheet("""
        QMainWindow { background-color: #2b2b2b; }
        QWidget { background-color: #2b2b2b; color: #e0e0e0; }
        QGroupBox {
            border: 1px solid #555;
            border-radius: 5px;
            margin-top: 10px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        QPushButton {
            background-color: #4a4a4a;
            border: 1px solid #666;
            border-radius: 3px;
            padding: 5px 10px;
            color: #e0e0e0;
        }
        QPushButton:hover { background-color: #5a5a5a; }
        QPushButton:pressed { background-color: #3a3a3a; }
        QPushButton:disabled { background-color: #333; color: #666; }
        QComboBox, QListWidget, QTreeWidget, QPlainTextEdit {
            background-color: #1e1e1e;
            border: 1px solid #555;
        }
        QCheckBox { spacing: 5px; }
        QProgressBar {
            border: 1px solid #555;
            border-radius: 3px;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: #4CAF50;
        }
        QHeaderView::section {
            background-color: #3a3a3a;
            border: 1px solid #555;
            padding: 2px 6px;
        }
    """)

    window = N64PatcherGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
