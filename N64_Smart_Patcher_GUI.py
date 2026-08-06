"""
N64_Smart_Patcher_GUI.py (BULLETPROOF V3.0)
Universal N64 ROM Inspector & Smart Patcher v3.0 - PyQt6 GUI
"""
import os
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QListWidget, QFileDialog,
    QProgressBar, QGroupBox, QFrame, QTreeWidget, QTreeWidgetItem, 
    QSplitter, QComboBox, QMessageBox, QTabWidget
)
from PyQt6.QtCore import Qt, QThread, QSettings, pyqtSignal
from PyQt6.QtGui import QFont, QIcon

import n64_core as core
from presets import list_presets, apply_preset, PRESETS, get_preset_warnings
from zip_handler import is_archive, extract_roms_from_archive, cleanup_temp_dir
from header_utils import detect_and_strip_scene_header, fix_rom_crc, get_rom_info_from_header


class PatchWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    log_message = pyqtSignal(str)
    
    def __init__(self, roms, options, strip_header=False, fix_crc=False):
        super().__init__()
        self.roms = roms
        self.options = options
        self.strip_header = strip_header
        self.fix_crc = fix_crc
        self.should_cancel = False
    
    def cancel(self):
        self.should_cancel = True
    
    def run(self):
        results = {"patched": 0, "skipped": 0, "errors": 0, "details": []}
        total = len(self.roms)
        
        for i, rom in enumerate(self.roms, 1):
            if self.should_cancel:
                self.log_message.emit("⛔ Abgebrochen durch User")
                break
            
            filename = os.path.basename(rom)
            self.progress.emit(i, total, filename)
            working_rom = rom
            
            try:
                # Header-Stripping
                if self.strip_header:
                    temp_stripped = rom + ".stripped.z64"
                    header_result = detect_and_strip_scene_header(rom, temp_stripped)
                    if header_result.get("stripped"):
                        self.log_message.emit(f"🔧 Header entfernt: {filename}")
                        working_rom = temp_stripped
                
                # Patching
                result = core.patch_rom(
                    working_rom, 
                    self.options,
                    log=lambda m: self.log_message.emit(f"   {m}"),
                    should_cancel=lambda: self.should_cancel
                )
                
                if not isinstance(result, dict):
                    result = {"status": "error", "message": "Invalid patch result", "output": None}
                
                out_file = result.get("output")
                
                # CRC-Fix
                if self.fix_crc and result.get("status") == "patched" and out_file and os.path.isfile(out_file):
                    crc_result = fix_rom_crc(out_file, core.RN64CRC_PATH)
                    self.log_message.emit(f"🔧 {crc_result.get('message', 'CRC Updated')}: {filename}")
                
                results["details"].append(result)
                if result.get("status") == "patched":
                    results["patched"] += 1
                    out_name = os.path.basename(out_file) if out_file else "patched.z64"
                    self.log_message.emit(f"✅ {filename} -> {out_name}")
                elif result.get("status") == "skipped":
                    results["skipped"] += 1
                    self.log_message.emit(f"⏭️  {filename}: {result.get('message', 'Skipped')}")
                else:
                    results["errors"] += 1
                    self.log_message.emit(f"❌ {filename}: {result.get('message', 'Error')}")
            
            except Exception as e:
                results["errors"] += 1
                self.log_message.emit(f"❌ Error on {filename}: {str(e)}")
            
            finally:
                # Cleanup gestripptes ROM
                if self.strip_header and working_rom != rom and os.path.isfile(working_rom):
                    try:
                        os.remove(working_rom)
                    except Exception:
                        pass
        
        self.finished.emit(results)


class N64PatcherGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Universal N64 ROM Inspector & Smart Patcher v3.0")
        self.setGeometry(100, 100, 900, 700)
        
        icon_path = core.get_asset_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.settings = QSettings("inf1nit3", "N64SmartPatcher")
        self.rom_list = []
        self.temp_dirs = []
        self.worker = None
        
        self.init_ui()
        self.load_settings()
    
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Tabs für bessere Übersicht
        tabs = QTabWidget()
        layout.addWidget(tabs)
        
        # Tab 1: Haupt-Patching
        patch_tab = QWidget()
        patch_layout = QVBoxLayout(patch_tab)
        tabs.addTab(patch_tab, "🎮 Patching")
        
        # Preset-Auswahl
        preset_group = QGroupBox("📋 Preset-Profil wählen")
        preset_layout = QVBoxLayout()
        
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("⚙️ Benutzerdefiniert (Individual Settings)", "custom")
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
        
        # Optionen
        options_group = QGroupBox("🎨 Visuelle Filter (Individual Settings)")
        options_layout = QVBoxLayout()
        
        self.cb_no_aa = QCheckBox("Anti-Aliasing entfernen (No-AA) - Schärfere Kanten")
        self.cb_no_dither = QCheckBox("Dither-Filter entfernen - Keine 16-bit Artefakte")
        self.cb_no_divot = QCheckBox("Divot-Filter entfernen - Keine Edge-Blurring")
        self.cb_no_gamma = QCheckBox("Gamma-Boost entfernen - Akkurate Farben")
        self.cb_hires = QCheckBox("High-Res 640x480 (Smart VI Table Engine)")
        
        for cb in [self.cb_no_aa, self.cb_no_dither, self.cb_no_divot, self.cb_no_gamma, self.cb_hires]:
            options_layout.addWidget(cb)
        
        options_group.setLayout(options_layout)
        patch_layout.addWidget(options_group)
        
        # Flashcart-Optionen
        flashcart_group = QGroupBox("💾 Flashcart-Optionen")
        flashcart_layout = QVBoxLayout()
        
        self.cb_strip_header = QCheckBox("Scene-Header entfernen (iN0000 etc.)")
        self.cb_fix_crc = QCheckBox("CRC1/CRC2 reparieren (EverDrive kompatibel)")
        
        flashcart_layout.addWidget(self.cb_strip_header)
        flashcart_layout.addWidget(self.cb_fix_crc)
        
        flashcart_group.setLayout(flashcart_layout)
        patch_layout.addWidget(flashcart_group)
        
        # ROM-Liste
        list_group = QGroupBox("📁 ROM-Bibliothek")
        list_layout = QVBoxLayout()
        
        self.rom_list_widget = QListWidget()
        list_layout.addWidget(self.rom_list_widget)
        
        btn_layout = QHBoxLayout()
        self.btn_add_files = QPushButton("➕ Dateien hinzufügen")
        self.btn_add_folder = QPushButton("📂 Ordner hinzufügen")
        self.btn_clear = QPushButton("🗑️ Liste leeren")
        
        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_add_folder.clicked.connect(self.add_folder)
        self.btn_clear.clicked.connect(self.clear_list)
        
        btn_layout.addWidget(self.btn_add_files)
        btn_layout.addWidget(self.btn_add_folder)
        btn_layout.addWidget(self.btn_clear)
        
        list_layout.addLayout(btn_layout)
        list_group.setLayout(list_layout)
        patch_layout.addWidget(list_group)
        
        # Progress & Buttons
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        patch_layout.addWidget(self.progress_bar)
        
        action_layout = QHBoxLayout()
        self.btn_inspect = QPushButton("🔍 Nur Inspizieren")
        self.btn_patch = QPushButton("🚀 Patchen starten")
        self.btn_cancel = QPushButton("⛔ Abbrechen")
        self.btn_cancel.setEnabled(False)
        
        self.btn_inspect.clicked.connect(self.inspect_roms)
        self.btn_patch.clicked.connect(self.start_patching)
        self.btn_cancel.clicked.connect(self.cancel_patching)
        
        action_layout.addWidget(self.btn_inspect)
        action_layout.addWidget(self.btn_patch)
        action_layout.addWidget(self.btn_cancel)
        
        patch_layout.addLayout(action_layout)
        
        # Tab 2: Log
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        tabs.addTab(log_tab, "📜 Log")
        
        self.log_widget = QListWidget()
        self.log_widget.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_widget)
        
        self.log(f"🎮 N64 Smart Patcher v3.0 gestartet")
        self.log(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    def on_preset_changed(self, index):
        preset_key = self.preset_combo.currentData()
        
        if preset_key == "custom":
            self.preset_warning_label.setText("")
            for cb in [self.cb_no_aa, self.cb_no_dither, self.cb_no_divot, self.cb_no_gamma, self.cb_hires]:
                cb.setEnabled(True)
        else:
            options = apply_preset(preset_key)
            self.cb_no_aa.setChecked(options.no_aa)
            self.cb_no_dither.setChecked(options.no_dither)
            self.cb_no_divot.setChecked(options.no_divot)
            self.cb_no_gamma.setChecked(options.no_gamma)
            self.cb_hires.setChecked(options.hires)
            
            for cb in [self.cb_no_aa, self.cb_no_dither, self.cb_no_divot, self.cb_no_gamma, self.cb_hires]:
                cb.setEnabled(False)
            
            warnings = get_preset_warnings(preset_key, "emulator")
            if warnings:
                self.preset_warning_label.setText("\n".join(f"⚠️ {w}" for w in warnings))
            else:
                self.preset_warning_label.setText("")
    
    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "ROM-Dateien auswählen", "",
            "N64 ROMs & Archive (*.z64 *.v64 *.n64 *.zip *.7z);;Alle Dateien (*)"
        )
        
        for file in files:
            try:
                if is_archive(file):
                    self.log(f"📦 Extrahiere Archiv: {os.path.basename(file)}")
                    temp_dir = os.path.join(os.path.dirname(file) or ".", "_n64_temp_extract")
                    extracted = extract_roms_from_archive(file, temp_dir)
                    self.temp_dirs.append(temp_dir)
                    for rom in extracted:
                        if rom not in self.rom_list:
                            self.rom_list.append(rom)
                            self.rom_list_widget.addItem(os.path.basename(rom))
                    self.log(f"   ✓ {len(extracted)} ROM(s) extrahiert")
                elif core.is_rom_file(file):
                    if file not in self.rom_list:
                        self.rom_list.append(file)
                        self.rom_list_widget.addItem(os.path.basename(file))
            except Exception as e:
                self.log(f"⚠️ Fehler beim Hinzufügen von {file}: {e}")
        
        self.log(f"📊 {len(self.rom_list)} ROM(s) in der Liste")
    
    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Ordner auswählen")
        if folder:
            for root, _, files in os.walk(folder):
                for file in files:
                    full_path = os.path.join(root, file)
                    try:
                        if is_archive(full_path):
                            self.log(f"📦 Extrahiere: {file}")
                            temp_dir = os.path.join(root, "_n64_temp_extract")
                            extracted = extract_roms_from_archive(full_path, temp_dir)
                            self.temp_dirs.append(temp_dir)
                            for rom in extracted:
                                if rom not in self.rom_list:
                                    self.rom_list.append(rom)
                                    self.rom_list_widget.addItem(os.path.basename(rom))
                        elif core.is_rom_file(file):
                            if not core.is_tool_output(full_path):
                                if full_path not in self.rom_list:
                                    self.rom_list.append(full_path)
                                    self.rom_list_widget.addItem(file)
                    except Exception as e:
                        self.log(f"⚠️ Fehler bei Datei {file}: {e}")
            
            self.log(f"📊 {len(self.rom_list)} ROM(s) in der Liste")
    
    def clear_list(self):
        self.rom_list.clear()
        self.rom_list_widget.clear()
        self.log("🗑️ Liste geleert")
    
    def log(self, message):
        self.log_widget.addItem(message)
        self.log_widget.scrollToBottom()
    
    def inspect_roms(self):
        if not self.rom_list:
            QMessageBox.warning(self, "Keine ROMs", "Bitte zuerst ROMs hinzufügen!")
            return
        
        self.log("\n🔍 Inspiziere ROMs...")
        for rom in self.rom_list:
            try:
                info = core.inspect_rom_details(rom, with_hashes=True)
                res = "640x480" if info["is_hires_640x480"] else "320x240"
                aa = "No-AA" if info["no_aa"] else "AA"
                self.log(f"{info['filename']}: {info['title']} [{info['region']}] "
                         f"{info['format']} | {res} | {aa}")
            except Exception as e:
                self.log(f"⚠️ Fehler bei Inspektion von {os.path.basename(rom)}: {e}")
        
        self.log("\n✅ Inspektion abgeschlossen")
    
    def start_patching(self):
        if not self.rom_list:
            QMessageBox.warning(self, "Keine ROMs", "Bitte zuerst ROMs hinzufügen!")
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
        
        self.log(f"\n🚀 Starte Patching von {len(self.rom_list)} ROM(s)...")
        
        self.worker = PatchWorker(
            self.rom_list, 
            options,
            strip_header=self.cb_strip_header.isChecked(),
            fix_crc=self.cb_fix_crc.isChecked()
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.log_message.connect(self.log)
        self.worker.start()
    
    def update_progress(self, current, total, filename):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
    
    def cancel_patching(self):
        if self.worker:
            self.worker.cancel()
            self.log("⛔ Abbruch angefordert...")
    
    def on_finished(self, results):
        self.btn_patch.setEnabled(True)
        self.btn_inspect.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        
        self.log(f"\n{'='*60}")
        self.log(f"✅ Fertig! Patched: {results['patched']}, Skipped: {results['skipped']}, Errors: {results['errors']}")
        self.log(f"{'='*60}\n")
        
        QMessageBox.information(
            self, "Patching abgeschlossen",
            f"Patched: {results['patched']}\n"
            f"Skipped: {results['skipped']}\n"
            f"Errors: {results['errors']}"
        )
        
        for temp_dir in self.temp_dirs:
            cleanup_temp_dir(temp_dir)
        self.temp_dirs.clear()
    
    def load_settings(self):
        self.cb_no_aa.setChecked(self.settings.value("no_aa", True, type=bool))
        self.cb_no_dither.setChecked(self.settings.value("no_dither", True, type=bool))
        self.cb_no_divot.setChecked(self.settings.value("no_divot", False, type=bool))
        self.cb_no_gamma.setChecked(self.settings.value("no_gamma", False, type=bool))
        self.cb_hires.setChecked(self.settings.value("hires", False, type=bool))
        self.cb_strip_header.setChecked(self.settings.value("strip_header", False, type=bool))
        self.cb_fix_crc.setChecked(self.settings.value("fix_crc", False, type=bool))
    
    def save_settings(self):
        self.settings.setValue("no_aa", self.cb_no_aa.isChecked())
        self.settings.setValue("no_dither", self.cb_no_dither.isChecked())
        self.settings.setValue("no_divot", self.cb_no_divot.isChecked())
        self.settings.setValue("no_gamma", self.cb_no_gamma.isChecked())
        self.settings.setValue("hires", self.cb_hires.isChecked())
        self.settings.setValue("strip_header", self.cb_strip_header.isChecked())
        self.settings.setValue("fix_crc", self.cb_fix_crc.isChecked())
    
    def closeEvent(self, event):
        self.save_settings()
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
        QComboBox {
            background-color: #4a4a4a;
            border: 1px solid #666;
            border-radius: 3px;
            padding: 3px;
        }
        QCheckBox { spacing: 5px; }
        QListWidget { 
            background-color: #1e1e1e;
            border: 1px solid #555;
        }
        QProgressBar {
            border: 1px solid #555;
            border-radius: 3px;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: #4CAF50;
        }
    """)
    
    window = N64PatcherGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
