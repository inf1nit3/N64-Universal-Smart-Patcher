"""Console-era visual theme for the desktop app.

Evokes mid-90s games hardware without borrowing anything that belongs to
anyone: dark textured console plastic, chunky bevelled controls that travel
on press, cartridge-label panel headers, bitmap-era typography, and four
primary accent colours used to tell functions apart.

What this file must never contain, because those are trademarks:
  * the interlocking-N cube logo, or any recreation of it
  * the "Nintendo 64" wordmark, its lettering or official typefaces
  * character art, or anything implying endorsement or affiliation

Colours, bitmap system fonts and general 90s industrial styling are not
protectable. The four accent colours are used functionally - separating
tabs, sections and states - never arranged into a logo-like mark.
"""

from __future__ import annotations

# --- palette --------------------------------------------------------------
# Warm charcoal rather than neutral grey: that is the difference between a
# generic dark theme and moulded ABS plastic.
PLASTIC_DARK = "#17171B"      # deep recesses, data surfaces
PLASTIC = "#26262D"           # main shell
PLASTIC_LIGHT = "#33333C"     # raised controls
PLASTIC_HILIGHT = "#41414D"   # hover
BEVEL_LIGHT = "#55555F"       # top/left edge of a raised control
BEVEL_DARK = "#0D0D10"        # bottom/right edge

LABEL = "#EDE9DE"             # warm off-white, like a printed cart label
LABEL_DIM = "#948F85"
DISABLED = "#54545E"

# Primary accents. Generic colours, used to separate functions.
ACCENT_RED = "#D93B30"
ACCENT_BLUE = "#3D7FD6"
ACCENT_GREEN = "#46B055"
ACCENT_YELLOW = "#EFBB2A"

FOCUS = ACCENT_BLUE
DANGER = ACCENT_RED
OK = ACCENT_GREEN

#: Order used for the accent rule and per-section bars.
ACCENTS = (ACCENT_RED, ACCENT_BLUE, ACCENT_GREEN, ACCENT_YELLOW)

# --- typography -----------------------------------------------------------
# Fixedsys and Terminal are the genuine article: bitmap faces that shipped
# with Windows since 3.x, so the era reads correctly and nothing needs to be
# bundled or licensed. Everything after them is a graceful fallback for
# macOS and Linux, ending at the generic families.
MONO_STACK = ('"Fixedsys", "Terminal", "Consolas", "Lucida Console", '
              '"DejaVu Sans Mono", "Menlo", "Courier New", monospace')
#: Headings: a condensed grotesque reads like moulded case lettering.
DISPLAY_STACK = ('"Bahnschrift", "Arial Narrow", "Liberation Sans Narrow", '
                 '"DejaVu Sans Condensed", "Segoe UI", sans-serif')
BODY_STACK = '"Segoe UI", "Tahoma", "DejaVu Sans", sans-serif'


def accent_for(index: int) -> str:
    """Cycle the accent colours, for per-section bars."""
    return ACCENTS[index % len(ACCENTS)]


def stylesheet() -> str:
    """Qt stylesheet for the whole application."""
    return f"""
        QMainWindow, QDialog {{ background-color: {PLASTIC}; }}
        QWidget {{
            background-color: {PLASTIC};
            color: {LABEL};
            font-family: {BODY_STACK};
            font-size: 12px;
        }}

        /* Panels read as cartridge labels: a recessed body under a raised
           title plate, with a coloured spine set per section in code. */
        QGroupBox {{
            background-color: {PLASTIC_DARK};
            border: 2px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 3px;
            margin-top: 16px;
            padding: 14px 10px 10px 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 8px;
            padding: 3px 10px;
            background-color: {PLASTIC_LIGHT};
            border: 2px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 3px;
            color: {LABEL};
            font-family: {MONO_STACK};
            font-weight: bold;
        }}

        /* Buttons with real travel: the bevel inverts and the label shifts. */
        QPushButton {{
            background-color: {PLASTIC_LIGHT};
            border: 2px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 3px;
            padding: 8px 14px;
            color: {LABEL};
            font-family: {MONO_STACK};
            font-weight: bold;
        }}
        QPushButton:hover {{ background-color: {PLASTIC_HILIGHT}; }}
        QPushButton:pressed {{
            background-color: {PLASTIC_DARK};
            border-top-color: {BEVEL_DARK};
            border-left-color: {BEVEL_DARK};
            border-bottom-color: {BEVEL_LIGHT};
            border-right-color: {BEVEL_LIGHT};
            padding: 9px 13px 7px 15px;
        }}
        QPushButton:disabled {{
            background-color: {PLASTIC};
            color: {DISABLED};
            border-color: {BEVEL_DARK};
        }}
        QPushButton:focus {{ border-color: {FOCUS}; }}

        /* The run control is a round red START key, the way a 90s pad put
           it: domed plastic, recessed collar, sinks when pressed. A red
           circle labelled START is generic industrial design - no shape or
           mark here belongs to anyone. */
        QPushButton#startButton {{
            background-color: qradialgradient(
                cx: 0.5, cy: 0.32, radius: 0.85, fx: 0.5, fy: 0.22,
                stop: 0 #F0584C, stop: 0.55 {ACCENT_RED}, stop: 1 #8E211A);
            border: 3px solid {BEVEL_DARK};
            border-top-color: #6B2721;
            border-radius: 41px;
            color: #FFF1EE;
            font-family: {MONO_STACK};
            font-size: 13px;
            font-weight: bold;
            padding: 0;
        }}
        QPushButton#startButton:hover {{
            background-color: qradialgradient(
                cx: 0.5, cy: 0.32, radius: 0.85, fx: 0.5, fy: 0.22,
                stop: 0 #FF6D60, stop: 0.55 #E6463A, stop: 1 #9C2620);
        }}
        QPushButton#startButton:pressed {{
            background-color: qradialgradient(
                cx: 0.5, cy: 0.45, radius: 0.85, fx: 0.5, fy: 0.6,
                stop: 0 #A82A22, stop: 1 #7A1B15);
            border-top-color: {BEVEL_DARK};
            border-bottom-color: #6B2721;
            padding-top: 3px;
        }}
        QPushButton#startButton:disabled {{
            background-color: qradialgradient(
                cx: 0.5, cy: 0.32, radius: 0.85, fx: 0.5, fy: 0.22,
                stop: 0 #5A4340, stop: 1 #3A2A28);
            color: {DISABLED};
            border-color: {BEVEL_DARK};
        }}
        /* The collar the key sits in. */
        QFrame#startCollar {{
            background-color: {PLASTIC_LIGHT};
            border: 2px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-radius: 52px;
        }}
        QPushButton#dangerAction {{ border-bottom: 4px solid {ACCENT_RED}; }}
        QPushButton#dangerAction:hover {{ background-color: #4A3A38; }}
        QPushButton#dangerAction:disabled {{ border-bottom-color: {BEVEL_DARK}; }}

        /* Tabs as cartridge spines. */
        QTabWidget::pane {{
            border: 2px solid {BEVEL_DARK};
            border-radius: 3px;
            top: -2px;
        }}
        QTabBar::tab {{
            background-color: {PLASTIC};
            border: 2px solid {BEVEL_DARK};
            border-bottom: 4px solid {BEVEL_DARK};
            border-top-left-radius: 3px;
            border-top-right-radius: 3px;
            padding: 8px 18px;
            margin-right: 3px;
            color: {LABEL_DIM};
            font-family: {MONO_STACK};
            font-weight: bold;
        }}
        QTabBar::tab:hover {{ color: {LABEL}; background-color: {PLASTIC_LIGHT}; }}
        QTabBar::tab:selected {{
            background-color: {PLASTIC_DARK};
            color: {LABEL};
        }}
        QTabBar::tab:selected:first {{ border-bottom-color: {ACCENT_RED}; }}
        QTabBar::tab:selected:middle {{ border-bottom-color: {ACCENT_BLUE}; }}
        QTabBar::tab:selected:last {{ border-bottom-color: {ACCENT_YELLOW}; }}

        /* Recessed data surfaces. Monospace is for technical readouts -
           filenames, log lines, table cells. Prose stays in the body face,
           which also keeps the preset descriptions from forcing the window
           wider than its configured geometry. */
        QComboBox, QListWidget, QTreeWidget, QPlainTextEdit, QLineEdit {{
            background-color: {PLASTIC_DARK};
            border: 2px solid {BEVEL_DARK};
            border-radius: 2px;
            padding: 4px;
            selection-background-color: {ACCENT_BLUE};
            selection-color: #FFFFFF;
        }}
        QListWidget, QTreeWidget, QPlainTextEdit, QLineEdit {{
            font-family: {MONO_STACK};
        }}
        QComboBox {{ font-family: {BODY_STACK}; }}
        QComboBox:focus, QListWidget:focus, QTreeWidget:focus,
        QPlainTextEdit:focus {{ border-color: {FOCUS}; }}
        /* QComboBox::drop-down is deliberately NOT styled: touching that
           sub-control makes Qt stop drawing the arrow, leaving a blank box. */
        QComboBox QAbstractItemView {{
            background-color: {PLASTIC_DARK};
            border: 2px solid {BEVEL_LIGHT};
            selection-background-color: {ACCENT_BLUE};
        }}

        QTreeWidget::item {{ padding: 4px 3px; }}
        QTreeWidget::item:alternate {{ background-color: #1D1D22; }}
        QHeaderView::section {{
            background-color: {PLASTIC_LIGHT};
            border: none;
            border-right: 1px solid {BEVEL_DARK};
            border-bottom: 3px solid {ACCENT_BLUE};
            padding: 6px 8px;
            font-family: {MONO_STACK};
            font-weight: bold;
            color: {LABEL};
        }}

        /* Square, chunky, cartridge-contact green when engaged. */
        QCheckBox {{ spacing: 9px; padding: 4px; }}
        QCheckBox::indicator {{
            width: 15px;
            height: 15px;
            border: 2px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 2px;
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
        QCheckBox::indicator:unchecked:disabled {{ background-color: {PLASTIC}; }}
        QCheckBox:disabled {{ color: {DISABLED}; }}

        QProgressBar {{
            background-color: {PLASTIC_DARK};
            border: 2px solid {BEVEL_DARK};
            border-radius: 2px;
            text-align: center;
            color: {LABEL};
            font-family: {MONO_STACK};
            font-weight: bold;
            height: 20px;
        }}
        QProgressBar::chunk {{ background-color: {ACCENT_GREEN}; }}

        QScrollBar:vertical {{
            background-color: {PLASTIC_DARK};
            width: 14px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background-color: {PLASTIC_HILIGHT};
            border: 1px solid {BEVEL_DARK};
            border-radius: 2px;
            min-height: 28px;
        }}
        QScrollBar::handle:vertical:hover {{ background-color: {BEVEL_LIGHT}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar:horizontal {{
            background-color: {PLASTIC_DARK};
            height: 14px;
        }}
        QScrollBar::handle:horizontal {{
            background-color: {PLASTIC_HILIGHT};
            border: 1px solid {BEVEL_DARK};
            border-radius: 2px;
            min-width: 28px;
        }}

        /* Front panel: monospaced readout on a recessed strip. */
        QStatusBar {{
            background-color: {PLASTIC_DARK};
            border-top: 2px solid {BEVEL_DARK};
            color: {LABEL_DIM};
            font-family: {MONO_STACK};
        }}
        QStatusBar QLabel {{ background: transparent; }}
        QStatusBar::item {{ border: none; }}

        QToolTip {{
            background-color: {PLASTIC_DARK};
            color: {LABEL};
            border: 2px solid {ACCENT_YELLOW};
            padding: 6px;
            font-family: {MONO_STACK};
        }}

        /* Faceplate strip above the tabs. */
        QLabel#faceplateTitle {{
            color: {LABEL};
            font-family: {DISPLAY_STACK};
            font-size: 19px;
            font-weight: bold;
            background: transparent;
        }}
        QLabel#faceplateSub {{
            color: {LABEL_DIM};
            font-family: {MONO_STACK};
            font-size: 11px;
            background: transparent;
        }}
        QFrame#accentRule {{ border: none; }}
        QFrame#faceplate {{
            background-color: {PLASTIC_DARK};
            border: 2px solid {BEVEL_DARK};
            border-top-color: {BEVEL_LIGHT};
            border-left-color: {BEVEL_LIGHT};
            border-radius: 3px;
        }}
    """
