"""Universal N64 ROM Inspector & Smart Patcher v2.1 - PyQt6 GUI.

All patching/inspection logic lives in n64_core (pure stdlib); this file
is only presentation. A headless CLI is available in n64_patcher_cli.py.
"""

import os
import sys
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QListWidget, QFileDialog,
    QProgressBar, QGroupBox, QFrame, QTreeWidget, QTreeWidgetItem, QSplitter
)
from PyQt6.QtCore import Qt, QThread, QSettings, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QIcon

import n64_core as core

TREE_FILE_LIMIT = 30  # inspector tree shows at most this many ROMs


class InspectWorker(QThread):
    """Background ROM inspection so large batches don't freeze the UI."""
    inspected = pyqtSignal(dict)
    finished_scan = pyqtSignal(int)

    def __init__(self, file_paths):
        super().__init__()
        self.file_paths = file_paths
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        count = 0
        for fp in self.file_paths:
            if self._cancelled:
                break
            try:
                info = core.inspect_rom_details(fp, with_hashes=True)
            except Exception as e:
                info = {"filename": os.path.basename(fp), "path": fp, "error": str(e)}
            self.inspected.emit(info)
            count += 1
        self.finished_scan.emit(count)


class PatchWorker(QThread):
    progress = pyqtSignal(int, int, str, bool)
    finished_all = pyqtSignal(int, int, bool)

    def __init__(self, file_paths, options):
        super().__init__()
        self.file_paths = file_paths
        self.options = options
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.file_paths)
        patched_count = skipped_count = 0

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_lines = [
            "=" * 50,
            f"N64 Smart Patcher Run - {timestamp}",
            f"Total ROMs to process: {total}",
            f"Options: No-AA={self.options.no_aa} No-Dither={self.options.no_dither} "
            f"No-Divot={self.options.no_divot} No-Gamma={self.options.no_gamma} "
            f"Hi-Res={self.options.hires}",
            "Policy: Original files are ALWAYS preserved (never overwritten)",
            "=" * 50,
            "",
        ]

        for i, rom_path in enumerate(self.file_paths):
            if self._cancelled:
                break
            filename = os.path.basename(rom_path)
            log_lines.append(f"[{i + 1}/{total}] File: {rom_path}")

            res = core.patch_rom(rom_path, self.options,
                                 log=log_lines.append,
                                 should_cancel=lambda: self._cancelled)

            if res["status"] == "patched":
                patched_count += 1
                msg = f"[OK] {filename} -> {res['message']}"
                self.progress.emit(i + 1, total, msg, True)
                log_lines.append(f"  Final Status: CREATED -> {res['output']}")
            elif res["status"] == "cancelled":
                log_lines.append("  Final Status: CANCELLED")
                break
            elif res["status"] == "skipped":
                skipped_count += 1
                msg = f"[i] {filename}\n   -- Skipped: {res['message']}"
                self.progress.emit(i + 1, total, msg, False)
                log_lines.append(f"  Final Status: SKIPPED - {res['message']}")
            else:
                skipped_count += 1
                msg = f"[X] {filename} Error: {res['message']}"
                self.progress.emit(i + 1, total, msg, False)
                log_lines.append(f"  Final Status: ERROR - {res['message']}")
            log_lines.append("")

        log_lines.append(f"Summary: Patched = {patched_count}, Skipped = {skipped_count}, "
                         f"Cancelled = {self._cancelled}\n")
        try:
            core.append_log(log_lines)
        except OSError as e:
            print(f"Failed to write log file: {e}")

        self.finished_all.emit(patched_count, skipped_count, self._cancelled)


class DropZone(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setToolTip("Drag & Drop N64 ROMs (.z64, .n64, .v64) or entire game folders here! Original files are NEVER modified.")
        self.setStyleSheet("""
            QFrame {
                border: 3px dashed #9d4edd;
                border-radius: 16px;
                background-color: #1a1625;
            }
            QFrame:hover {
                border: 3px dashed #c77dff;
                background-color: #241e33;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel("\U0001F47E")
        icon_label.setFont(QFont("Segoe UI Emoji", 44))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("border: none; background: transparent;")

        text_label = QLabel("Drag & Drop N64 ROMs Here")
        text_label.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        text_label.setStyleSheet("color: #e0aaff; border: none; background: transparent;")
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_label = QLabel("Original ROMs are NEVER overwritten. Always creates a new patched file!")
        sub_label.setFont(QFont("Segoe UI", 10))
        sub_label.setStyleSheet("color: #00f0ff; border: none; background: transparent;")
        sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(icon_label)
        layout.addWidget(text_label)
        layout.addWidget(sub_label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        files = []
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                for root, _, filenames in os.walk(path):
                    for fn in filenames:
                        full = os.path.join(root, fn)
                        if core.is_rom_file(full) and not core.is_tool_output(full):
                            files.append(full)
            elif os.path.isfile(path):
                if core.is_rom_file(path) and not core.is_tool_output(path):
                    files.append(path)

        if files:
            self.files_dropped.emit(files)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Universal N64 ROM Inspector & Smart Patcher v2.1")
        self.resize(1080, 780)

        icon_path = core.get_asset_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setStyleSheet("""
            QMainWindow {
                background-color: #100c19;
            }
            QLabel {
                color: #ffffff;
            }
            QToolTip {
                background-color: #241e33;
                color: #e0aaff;
                border: 2px solid #c77dff;
                border-radius: 8px;
                padding: 10px;
                font-size: 13px;
                font-family: "Segoe UI", sans-serif;
            }
            QGroupBox {
                border: 2px solid #5a189a;
                border-radius: 10px;
                margin-top: 14px;
                font-weight: bold;
                color: #c77dff;
                background-color: #161224;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                background-color: #100c19;
            }

            QCheckBox {
                color: #8d99ae;
                font-size: 12px;
                font-weight: bold;
                padding: 6px 8px;
                border-radius: 6px;
                background-color: #1a1625;
                border: 1px solid #3c096c;
            }
            QCheckBox:hover {
                border: 1px solid #9d4edd;
                color: #ffffff;
                background-color: #241e33;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #5a189a;
                background-color: #100c19;
            }
            QCheckBox::indicator:unchecked:hover {
                border: 2px solid #9d4edd;
            }
            QCheckBox::indicator:checked {
                background-color: #00f0ff;
                border: 2px solid #ffffff;
                image: none;
            }
            QCheckBox:checked {
                color: #ffffff;
                background-color: #3c096c;
                border: 1.5px solid #00f0ff;
            }

            QPushButton {
                background-color: #5a189a;
                color: #ffffff;
                font-size: 13px;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px 18px;
                border: 1px solid #7b2cbf;
            }
            QPushButton:hover {
                background-color: #7b2cbf;
                border: 1px solid #c77dff;
            }

            QPushButton#btn_patch {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7b2cbf, stop:1 #9d4edd);
                color: #ffffff;
                border: 2px solid #00f0ff;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#btn_patch:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9d4edd, stop:1 #c77dff);
                border: 2px solid #ffffff;
            }
            QPushButton#btn_patch:disabled {
                background-color: #241e33;
                color: #5a506e;
                border: 1px solid #3c324d;
            }
            QPushButton#btn_cancel {
                background-color: #5e2b25;
                border: 2px solid #ff6b5e;
            }
            QPushButton#btn_cancel:hover {
                background-color: #7a3a31;
            }

            QTreeWidget {
                background-color: #161224;
                border: 2px solid #3c096c;
                border-radius: 10px;
                color: #e0aaff;
                font-family: Consolas, "Segoe UI";
                font-size: 12px;
            }
            QTreeWidget::item {
                padding: 6px;
                border-bottom: 1px solid #241e33;
            }
            QTreeWidget::item:selected {
                background-color: #7b2cbf;
                color: #ffffff;
                font-weight: bold;
            }
            QListWidget {
                background-color: #161224;
                border: 2px solid #3c096c;
                border-radius: 10px;
                color: #e0aaff;
                font-family: Consolas, "Courier New";
                font-size: 12px;
            }
            QListWidget::item:selected {
                background-color: #7b2cbf;
                color: #ffffff;
            }
            QProgressBar {
                border: 1px solid #3c096c;
                border-radius: 6px;
                text-align: center;
                color: white;
                background-color: #161224;
            }
            QProgressBar::chunk {
                background-color: #00f0ff;
                border-radius: 5px;
            }
        """)

        self.settings = QSettings("N64SmartPatcher", "N64SmartPatcher")
        self.loaded_file_paths = []
        self.inspected_infos = []
        self.inspect_worker = None
        self.patch_worker = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(12)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Universal N64 Smart Patcher v2.1")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #c77dff;")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        btn_export = QPushButton("Export Report...")
        btn_export.setToolTip("Exports the current inspection results to a CSV or JSON report.")
        btn_export.clicked.connect(self.export_report)
        header_layout.addWidget(btn_export)

        btn_open_log = QPushButton("Open Log File")
        btn_open_log.setToolTip("Opens the detailed execution log file of recent patch runs.")
        btn_open_log.clicked.connect(self.open_log_file)
        header_layout.addWidget(btn_open_log)

        main_layout.addLayout(header_layout)

        # Drop Zone
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.load_and_inspect_files)
        main_layout.addWidget(self.drop_zone, stretch=2)

        # Options Group Box
        opts_group = QGroupBox("Patch Options (Cyan highlight = ACTIVE)")
        opts_layout = QHBoxLayout(opts_group)

        self.cb_no_aa = QCheckBox("Disable Anti-Aliasing (No-AA)")
        self.cb_no_aa.setToolTip("Disables blurry N64 Video Interface Anti-Aliasing for crisp 3D polygon edges.\nUses proven u64aap.exe community tool (dynamic matcher as fallback).")

        self.cb_no_dither = QCheckBox("Disable Dither Filter")
        self.cb_no_dither.setToolTip("Disables the 16-bit dithering dot pattern across textures and color gradients.")

        self.cb_divot = QCheckBox("Disable Divot Filter")
        self.cb_divot.setToolTip("Disables hardware edge blurring on 3D object boundaries.\nRequires the u64aap AA stage (No-AA must be enabled).")

        self.cb_gamma = QCheckBox("Disable Gamma Boost")
        self.cb_gamma.setToolTip("Disables the N64 hardware gamma boost for more accurate colors.\nRequires the u64aap AA stage (No-AA must be enabled).")

        self.cb_hires = QCheckBox("Enable 640x480 Hi-Res")
        self.cb_hires.setToolTip(
            "Patches N64 VI mode tables from 320x240 to 640x480 resolution.\n\n"
            "Smart VI Table Engine: Identifies real video configuration\n"
            "tables using NTSC/PAL burst timing signatures.\n"
            "Zero false positives - only confirmed VI tables are modified.\n\n"
            "For supported games (SM64, GoldenEye, Banjo-Kazooie, F-Zero X,\n"
            "Forsaken 64, Pokemon Snap, Quake II), SubDrag's verified\n"
            ".xdelta patches are preferred automatically and applied to the\n"
            "clean ROM first.\n\n"
            "Requires Expansion Pak on real hardware."
        )

        self.cb_no_aa.setChecked(self.settings.value("no_aa", True, type=bool))
        self.cb_no_dither.setChecked(self.settings.value("no_dither", True, type=bool))
        self.cb_divot.setChecked(self.settings.value("no_divot", True, type=bool))
        self.cb_gamma.setChecked(self.settings.value("no_gamma", False, type=bool))
        self.cb_hires.setChecked(self.settings.value("hires", False, type=bool))

        self.cb_no_aa.toggled.connect(lambda c: self.settings.setValue("no_aa", c))
        self.cb_no_dither.toggled.connect(lambda c: self.settings.setValue("no_dither", c))
        self.cb_divot.toggled.connect(lambda c: self.settings.setValue("no_divot", c))
        self.cb_gamma.toggled.connect(lambda c: self.settings.setValue("no_gamma", c))
        self.cb_hires.toggled.connect(lambda c: self.settings.setValue("hires", c))

        lbl_safety = QLabel("Original Files Preserved")
        lbl_safety.setStyleSheet("color: #00f0ff; font-weight: bold; font-size: 12px; padding: 6px 10px; background-color: #1a1625; border: 1px solid #00f0ff; border-radius: 6px;")
        lbl_safety.setToolTip("Original ROMs are NEVER modified or overwritten. A new patched file ( [NoAA].z64 / [640p].z64 / [HR+NoAA].z64) is created every time.")

        opts_layout.addWidget(self.cb_no_aa)
        opts_layout.addWidget(self.cb_no_dither)
        opts_layout.addWidget(self.cb_divot)
        opts_layout.addWidget(self.cb_gamma)
        opts_layout.addWidget(self.cb_hires)
        opts_layout.addWidget(lbl_safety)
        main_layout.addWidget(opts_group)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.inspector_tree = QTreeWidget()
        self.inspector_tree.setHeaderLabels(["ROM Property / Audit", "Value / Status"])
        self.inspector_tree.setColumnWidth(0, 280)
        self.inspector_tree.setToolTip("Hover your mouse over individual properties for detailed explanations.")
        splitter.addWidget(self.inspector_tree)

        self.log_list = QListWidget()
        self.log_list.addItem("Ready. Drag & drop ROMs. Original files are NEVER overwritten...")
        splitter.addWidget(self.log_list)

        splitter.setSizes([400, 500])
        main_layout.addWidget(splitter, stretch=4)

        # Buttons
        btn_layout = QHBoxLayout()

        self.btn_select = QPushButton("Select ROMs...")
        self.btn_select.setToolTip("Opens file picker dialog to select N64 ROM files.")
        self.btn_select.clicked.connect(self.select_files_dialog)
        btn_layout.addWidget(self.btn_select)

        self.btn_patch = QPushButton("PATCH ROMS NOW")
        self.btn_patch.setObjectName("btn_patch")
        self.btn_patch.setToolTip("Starts silent background patching process for all loaded ROM files.")
        self.btn_patch.clicked.connect(self.run_patching)
        self.btn_patch.setEnabled(False)
        btn_layout.addWidget(self.btn_patch)

        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setObjectName("btn_cancel")
        self.btn_cancel.setToolTip("Stops the running batch after the current ROM.")
        self.btn_cancel.clicked.connect(self.cancel_patching)
        self.btn_cancel.setVisible(False)
        btn_layout.addWidget(self.btn_cancel)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        btn_layout.addWidget(self.progress_bar)

        main_layout.addLayout(btn_layout)

        # Warn about missing bundled tools
        tools = core.check_tools()
        missing = [name for name in ("u64aap", "rn64crc", "xdelta3") if not tools[name]]
        if missing:
            self.log_list.addItem(f"WARNING: missing bundled tools: {', '.join(missing)}. "
                                  "Affected stages will degrade or be skipped.")

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def load_and_inspect_files(self, file_paths):
        self.loaded_file_paths = file_paths
        self.inspected_infos = []
        self.inspector_tree.clear()
        self.log_list.clear()

        self.log_list.addItem(f"{len(file_paths)} ROM(s) loaded. Inspecting in background...")
        self.log_list.addItem("Original files are NEVER overwritten. Click 'PATCH ROMS NOW' to generate new patched files!\n")
        self.btn_patch.setEnabled(False)

        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
            self.inspect_worker.wait(2000)

        self.inspect_worker = InspectWorker(file_paths)
        self.inspect_worker.inspected.connect(self.on_rom_inspected)
        self.inspect_worker.finished_scan.connect(self.on_inspect_finished)
        self.inspect_worker.start()

    def on_rom_inspected(self, info):
        self.inspected_infos.append(info)
        if "error" in info:
            self.log_list.addItem(f"  {info['filename']} - inspection error: {info['error']}")
            return

        if len(self.inspected_infos) <= TREE_FILE_LIMIT:
            self.add_rom_to_tree(info)

        if info["is_60fps_or_mod"]:
            st = "60FPS/Mod (Sharp)"
        else:
            st = "No-AA (Sharp)" if info["no_aa"] else "Standard AA (Blurry)"
        vi_str = f"{info['vi_table_count']} VI tables" if info["vi_table_count"] > 0 else "no VI tables"
        sd_str = " [SubDrag patch]" if info["has_subdrag_patch"] else ""
        res_str = "640x480" if info["is_hires_640x480"] else "320x240"
        self.log_list.addItem(f"  {info['filename']} [{info['region']}] - {res_str} | {vi_str}{sd_str} | {st}")

    def on_inspect_finished(self, count):
        if count > TREE_FILE_LIMIT:
            self.log_list.addItem(f"(Inspector tree shows the first {TREE_FILE_LIMIT} of {count} ROMs.)")
        self.log_list.addItem(f"Inspection complete ({count} ROMs). Ready to patch.")
        self.btn_patch.setEnabled(count > 0)

    def add_rom_to_tree(self, info):
        rom_item = QTreeWidgetItem(self.inspector_tree, [info["filename"], f"{info['size_mb']} MB"])
        rom_item.setToolTip(0, f"Full Path:\n{info['path']}")
        rom_item.setExpanded(True)

        item_title = QTreeWidgetItem(rom_item, ["Game Title", info["title"]])
        item_title.setToolTip(0, "Internal game title stored in N64 ROM header (max 20 ASCII chars).")
        item_title.setToolTip(1, f"Header Name: '{info['title']}'")

        item_reg = QTreeWidgetItem(rom_item, ["Region / TV Standard", info["region"]])
        item_reg.setToolTip(0, "Shows display standard & region (NTSC = 60Hz, PAL = 50Hz).")

        item_id = QTreeWidgetItem(rom_item, ["Game ID / Code", info["game_id"]])
        item_id.setToolTip(0, "Official 4-character Nintendo Game ID code.")
        item_id.setToolTip(1, f"Game Code: {info['game_id']}")

        vi_count = info["vi_table_count"]
        if info["is_hires_640x480"]:
            res_status = "640x480 Hi-Res (native or already patched)"
            res_tip = "This ROM already uses 640-wide VI mode tables (native hi-res or previously patched)."
        elif vi_count > 0:
            res_status = f"320x240 Standard ({vi_count} VI tables patchable)"
            res_tip = f"Found {vi_count} VI mode tables that can be safely upgraded to 640x480."
        else:
            res_status = "320x240 (No VI tables detected)"
            res_tip = "No standard VI mode tables found. ROM may use compressed or custom video init."
        item_res = QTreeWidgetItem(rom_item, ["Video Resolution", res_status])
        item_res.setToolTip(0, res_tip)
        item_res.setToolTip(1, res_tip)

        if info["has_subdrag_patch"]:
            item_sd = QTreeWidgetItem(rom_item, ["SubDrag Hi-Res Patch", "AVAILABLE (verified .xdelta)"])
            item_sd.setToolTip(0, "A manually verified 640x480 patch by SubDrag exists for this game.")
            item_sd.setToolTip(1, "Applied to the clean ROM first, before any other modifications.")

        item_fmt = QTreeWidgetItem(rom_item, ["Format & Endianness", info["format"]])
        item_fmt.setToolTip(0, "ROM Format:\n.z64 (Big-Endian): Native N64 format\n.v64 (Byte-Swapped): N64 Doctor\n.n64 (Little-Endian): Partner 64")
        item_fmt.setToolTip(1, "The tool automatically converts all formats to native .z64!")

        item_crc = QTreeWidgetItem(rom_item, ["Boot Checksum (CRC1/CRC2)", f"0x{info['crc1']} / 0x{info['crc2']}"])
        item_crc.setToolTip(0, "N64 Hardware Boot Checksums in header.")
        item_crc.setToolTip(1, "Recalculated automatically after patching to prevent black screens.")

        if "md5" in info:
            item_md5 = QTreeWidgetItem(rom_item, ["MD5", info["md5"]])
            item_md5.setToolTip(0, "MD5 hash of the ROM file - compare against No-Intro databases to verify a clean dump.")
            item_sha1 = QTreeWidgetItem(rom_item, ["SHA-1", info["sha1"]])
            item_sha1.setToolTip(0, "SHA-1 hash of the ROM file - compare against No-Intro databases to verify a clean dump.")

        if info["is_60fps_or_mod"]:
            aa_status = "60FPS / Mod ROM (Natively Sharp)"
            dither_status = "60FPS / Mod ROM (Dither Free)"
            aa_tip = "This 60FPS/Mod ROM was already coded without N64 Anti-Aliasing by the modder!"
            dither_tip = "Dithering was already removed in this mod by the developer."
        else:
            aa_status = "No-AA Active (Crisp)" if info["no_aa"] else "Standard AA (Blurry)"
            dither_status = "Dither OFF" if info["no_dither"] else "Dither ON (Dot Pattern)"
            aa_tip = "When 'No-AA Active', polygon edges are rendered crisp and sharp without N64 blur."
            dither_tip = "When 'Dither OFF', annoying 16-bit dot patterns on textures and gradients are removed."

        item_aa = QTreeWidgetItem(rom_item, ["Anti-Aliasing (AA) Status", aa_status])
        item_aa.setToolTip(0, aa_tip)
        item_aa.setToolTip(1, aa_tip)

        item_dit = QTreeWidgetItem(rom_item, ["Dither Filter Status", dither_status])
        item_dit.setToolTip(0, dither_tip)
        item_dit.setToolTip(1, dither_tip)

    # ------------------------------------------------------------------
    # Patching
    # ------------------------------------------------------------------

    def run_patching(self):
        if not self.loaded_file_paths:
            return

        self.log_list.addItem(f"\nStarting patcher for {len(self.loaded_file_paths)} ROM(s)...")
        self.progress_bar.setMaximum(len(self.loaded_file_paths))
        self.progress_bar.setValue(0)
        self.btn_select.setEnabled(False)
        self.btn_patch.setVisible(False)
        self.btn_cancel.setVisible(True)

        options = core.PatchOptions(
            no_aa=self.cb_no_aa.isChecked(),
            no_dither=self.cb_no_dither.isChecked(),
            no_divot=self.cb_divot.isChecked(),
            no_gamma=self.cb_gamma.isChecked(),
            hires=self.cb_hires.isChecked(),
        )

        self.patch_worker = PatchWorker(self.loaded_file_paths, options)
        self.patch_worker.progress.connect(self.on_progress)
        self.patch_worker.finished_all.connect(self.on_finished)
        self.patch_worker.start()

    def cancel_patching(self):
        if self.patch_worker and self.patch_worker.isRunning():
            self.patch_worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.log_list.addItem("Cancelling after current ROM...")

    def on_progress(self, current, total, message, success):
        self.progress_bar.setValue(current)
        self.log_list.addItem(message)
        self.log_list.scrollToBottom()

    def on_finished(self, patched, skipped, cancelled):
        self.btn_select.setEnabled(True)
        self.btn_patch.setVisible(True)
        self.btn_patch.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        if cancelled:
            self.log_list.addItem(f"\nCancelled. {patched} new patched ROM(s) created, {skipped} skipped.")
        else:
            self.log_list.addItem(f"\nDone! {patched} new patched ROM(s) created, {skipped} skipped.")
        self.log_list.addItem(f"Detailed log saved at:\n   {core.get_log_path()}")

        # Re-inspect so the tree reflects the new on-disk state
        if self.loaded_file_paths:
            self.load_and_inspect_files(self.loaded_file_paths)

    # ------------------------------------------------------------------
    # Misc UI actions
    # ------------------------------------------------------------------

    def open_log_file(self):
        path = core.get_log_path()
        if not os.path.exists(path):
            self.log_list.addItem("No log file available yet. Run a patch operation first.")
            return
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])

    def export_report(self):
        if not self.inspected_infos:
            self.log_list.addItem("Nothing to export - load and inspect ROMs first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Inspection Report", "n64_report.csv",
            "CSV Report (*.csv);;JSON Report (*.json)"
        )
        if not path:
            return
        try:
            core.export_report(self.inspected_infos, path)
            self.log_list.addItem(f"Report exported to {path}")
        except OSError as e:
            self.log_list.addItem(f"Export failed: {e}")

    def select_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select N64 ROMs", "", "N64 ROMs (*.z64 *.n64 *.v64);;All Files (*.*)"
        )
        files = [f for f in files if not core.is_tool_output(f)]
        if files:
            self.load_and_inspect_files(files)

    def closeEvent(self, event):
        for worker in (self.inspect_worker, self.patch_worker):
            if worker and worker.isRunning():
                worker.cancel()
                worker.wait(3000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
