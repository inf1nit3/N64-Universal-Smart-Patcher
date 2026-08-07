"""Console-era visual theme for the desktop app.

Deliberately evokes mid-90s games hardware without borrowing anything that
belongs to anyone: dark textured console plastic, chunky bevelled controls,
a warm off-white label text, and four primary accent colours used to tell
functions apart.

What this file must never contain, because those are trademarks:
  * the interlocking-N cube logo, or any recreation of it
  * the "Nintendo 64" wordmark, its lettering or official fonts
  * character art, or anything implying endorsement or affiliation

Colours and general 90s industrial styling are not protectable, and the
four accent colours below are used functionally - to distinguish tabs,
states and status - never arranged into a logo-like mark.
"""

# --- palette --------------------------------------------------------------
# Console plastic, slightly warm rather than a neutral grey, which is what
# makes the difference between "generic dark theme" and "moulded ABS".
PLASTIC_DARK = "#1E1E22"      # recessed areas, list backgrounds
PLASTIC = "#2A2A31"           # main body
PLASTIC_LIGHT = "#35353E"     # raised controls
PLASTIC_HILIGHT = "#43434E"   # hover
BEVEL_LIGHT = "#4E4E5A"       # top/left edge of a raised control
BEVEL_DARK = "#141418"        # bottom/right edge

LABEL = "#E8E4DA"             # warm off-white, like a printed cart label
LABEL_DIM = "#9A968E"
DISABLED = "#5C5C66"

# Primary accents. Generic colours, used to separate functions.
ACCENT_RED = "#D0342C"
ACCENT_BLUE = "#2F6FC4"
ACCENT_GREEN = "#3FA34D"
ACCENT_YELLOW = "#E5B02B"

FOCUS = ACCENT_BLUE
DANGER = ACCENT_RED
OK = ACCENT_GREEN


def stylesheet() -> str:
    """Qt stylesheet for the whole application."""
    return f"""
        QMainWindow, QDialog {{ background-color: {PLASTIC}; }}
        QWidget {{
            background-color: {PLASTIC};
            color: {LABEL};
            font-size: 12px;
        }}

        /* Panels read as moulded recesses in the console shell. */
        QGroupBox {{
            background-color: {PLASTIC_DARK};
            border: 1px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 4px;
            margin-top: 14px;
            padding: 12px 10px 10px 10px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 2px 8px;
            background-color: {PLASTIC_LIGHT};
            border: 1px solid {BEVEL_DARK};
            border-radius: 3px;
            color: {LABEL};
        }}

        /* Chunky, bevelled, with real travel on press. */
        QPushButton {{
            background-color: {PLASTIC_LIGHT};
            border: 1px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 4px;
            padding: 7px 14px;
            color: {LABEL};
            font-weight: bold;
        }}
        QPushButton:hover {{ background-color: {PLASTIC_HILIGHT}; }}
        QPushButton:pressed {{
            background-color: {PLASTIC_DARK};
            border-top-color: {BEVEL_DARK};
            border-left-color: {BEVEL_DARK};
            border-bottom-color: {BEVEL_LIGHT};
            border-right-color: {BEVEL_LIGHT};
            padding: 8px 13px 6px 15px;
        }}
        QPushButton:disabled {{
            background-color: {PLASTIC};
            color: {DISABLED};
            border-color: {BEVEL_DARK};
        }}
        QPushButton:focus {{ border: 1px solid {FOCUS}; }}

        /* Tabs as cartridge spines, each with its own accent. */
        QTabWidget::pane {{
            border: 1px solid {BEVEL_DARK};
            border-radius: 4px;
            top: -1px;
        }}
        QTabBar::tab {{
            background-color: {PLASTIC_LIGHT};
            border: 1px solid {BEVEL_DARK};
            border-bottom: 3px solid {BEVEL_DARK};
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            padding: 7px 16px;
            margin-right: 2px;
            color: {LABEL_DIM};
            font-weight: bold;
        }}
        QTabBar::tab:hover {{ color: {LABEL}; }}
        QTabBar::tab:selected {{
            background-color: {PLASTIC_DARK};
            color: {LABEL};
        }}
        QTabBar::tab:selected:first {{ border-bottom-color: {ACCENT_RED}; }}
        QTabBar::tab:selected:middle {{ border-bottom-color: {ACCENT_BLUE}; }}
        QTabBar::tab:selected:last {{ border-bottom-color: {ACCENT_YELLOW}; }}

        /* Recessed data surfaces. */
        QComboBox, QListWidget, QTreeWidget, QPlainTextEdit, QLineEdit {{
            background-color: {PLASTIC_DARK};
            border: 1px solid {BEVEL_DARK};
            border-top-color: {BEVEL_DARK};
            border-radius: 3px;
            padding: 4px;
            selection-background-color: {ACCENT_BLUE};
            selection-color: #FFFFFF;
        }}
        QComboBox:focus, QListWidget:focus, QTreeWidget:focus,
        QPlainTextEdit:focus {{ border: 1px solid {FOCUS}; }}
        /* QComboBox::drop-down is deliberately NOT styled: touching that
           sub-control makes Qt stop drawing the arrow, leaving a blank
           box, and re-supplying one means shipping an image. */
        QComboBox QAbstractItemView {{
            background-color: {PLASTIC_DARK};
            border: 1px solid {BEVEL_LIGHT};
            selection-background-color: {ACCENT_BLUE};
        }}

        QTreeWidget::item {{ padding: 3px; }}
        QTreeWidget::item:alternate {{ background-color: #232329; }}
        QHeaderView::section {{
            background-color: {PLASTIC_LIGHT};
            border: none;
            border-right: 1px solid {BEVEL_DARK};
            border-bottom: 2px solid {ACCENT_BLUE};
            padding: 5px 8px;
            font-weight: bold;
            color: {LABEL};
        }}

        QCheckBox {{ spacing: 8px; padding: 3px; }}
        QCheckBox::indicator {{
            width: 15px;
            height: 15px;
            border: 1px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 3px;
            background-color: {PLASTIC_DARK};
        }}
        QCheckBox::indicator:hover {{ border-color: {FOCUS}; }}
        QCheckBox::indicator:checked {{
            background-color: {ACCENT_GREEN};
            border-color: {BEVEL_DARK};
        }}
        /* A locked preset disables the boxes but they are still checked.
           Dim the fill rather than clearing it, or the UI misreports what
           it is about to do. */
        QCheckBox::indicator:checked:disabled {{
            background-color: #2E5E38;
            border-color: {BEVEL_DARK};
        }}
        QCheckBox::indicator:unchecked:disabled {{
            background-color: {PLASTIC};
        }}
        QCheckBox:disabled {{ color: {DISABLED}; }}

        QProgressBar {{
            background-color: {PLASTIC_DARK};
            border: 1px solid {BEVEL_DARK};
            border-radius: 3px;
            text-align: center;
            color: {LABEL};
            font-weight: bold;
            height: 18px;
        }}
        QProgressBar::chunk {{
            background-color: {ACCENT_GREEN};
            border-radius: 2px;
        }}

        QScrollBar:vertical {{
            background-color: {PLASTIC_DARK};
            width: 13px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background-color: {PLASTIC_HILIGHT};
            border-radius: 3px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{ background-color: {BEVEL_LIGHT}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar:horizontal {{
            background-color: {PLASTIC_DARK};
            height: 13px;
        }}
        QScrollBar::handle:horizontal {{
            background-color: {PLASTIC_HILIGHT};
            border-radius: 3px;
            min-width: 24px;
        }}

        QStatusBar {{
            background-color: {PLASTIC_DARK};
            border-top: 1px solid {BEVEL_DARK};
            color: {LABEL_DIM};
        }}
        QStatusBar QLabel {{ background: transparent; }}
        QToolTip {{
            background-color: {PLASTIC_DARK};
            color: {LABEL};
            border: 1px solid {ACCENT_YELLOW};
            padding: 6px;
        }}
    """
