from __future__ import annotations

from PySide6.QtCore import Qt


THEME_TOKENS = {
    "light": {
        "window": "#f3f7fb",
        "surface": "rgba(255, 255, 255, 0.94)",
        "surface_alt": "#ecf2f9",
        "border": "#d5dfeb",
        "text": "#1f2a37",
        "muted": "#66758a",
        "accent": "#0f6cbd",
        "accent_soft": "#dbeafe",
        "selection": "#d9ecff",
    },
    "dark": {
        "window": "#111826",
        "surface": "rgba(20, 29, 43, 0.96)",
        "surface_alt": "#182235",
        "border": "#283549",
        "text": "#f4f7fb",
        "muted": "#a8b4c5",
        "accent": "#6cb8ff",
        "accent_soft": "#16324d",
        "selection": "#1a4773",
    },
}


def resolve_theme_mode(app, requested: str) -> str:
    if requested in {"light", "dark"}:
        return requested
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except AttributeError:
        pass
    return "light"


def apply_theme(app, requested: str) -> str:
    mode = resolve_theme_mode(app, requested)
    palette = THEME_TOKENS[mode]
    app.setStyleSheet(
        """
        QWidget {
            background: %(window)s;
            color: %(text)s;
            font-family: "Segoe UI", "Microsoft YaHei";
            font-size: 10.5pt;
        }
        QMainWindow, QWidget#rootWindow {
            background: %(window)s;
        }
        QFrame#surfaceCard, QFrame#statCard {
            background: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 18px;
        }
        QLabel#sectionTitle {
            font-size: 15px;
            font-weight: 600;
            color: %(text)s;
            background: transparent;
        }
        QLabel#mutedLabel {
            color: %(muted)s;
            background: transparent;
        }
        QLabel#heroTitle {
            font-size: 24px;
            font-weight: 700;
            background: transparent;
        }
        QLabel#heroSubtitle {
            color: %(muted)s;
            background: transparent;
        }
        QLabel#filterPill {
            background: %(accent_soft)s;
            color: %(accent)s;
            border-radius: 11px;
            padding: 5px 12px;
            font-weight: 600;
        }
        QLineEdit, QComboBox, QSpinBox, QTextEdit, QListWidget, QTreeWidget, QTableView {
            background: %(surface_alt)s;
            border: 1px solid %(border)s;
            border-radius: 14px;
            padding: 6px 10px;
            selection-background-color: %(selection)s;
            selection-color: %(text)s;
            gridline-color: %(border)s;
        }
        QScrollArea {
            background: transparent;
            border: none;
        }
        QHeaderView::section {
            background: %(surface)s;
            color: %(muted)s;
            padding: 10px 12px;
            border: none;
            border-bottom: 1px solid %(border)s;
            font-weight: 600;
        }
        QPushButton {
            background: %(surface_alt)s;
            border: 1px solid %(border)s;
            border-radius: 12px;
            padding: 8px 14px;
        }
        QPushButton:hover {
            border-color: %(accent)s;
        }
        QPushButton#primaryButton {
            background: %(accent)s;
            color: white;
            border: none;
            font-weight: 600;
        }
        QPushButton#ghostButton {
            background: transparent;
        }
        QTabWidget::pane {
            border: none;
            background: transparent;
        }
        QTabBar::tab {
            background: %(surface_alt)s;
            color: %(muted)s;
            border-radius: 10px;
            padding: 8px 14px;
            margin-right: 8px;
        }
        QTabBar::tab:selected {
            background: %(accent_soft)s;
            color: %(accent)s;
            font-weight: 600;
        }
        QStatusBar {
            background: %(surface)s;
            color: %(muted)s;
            border-top: 1px solid %(border)s;
        }
        """
        % palette
    )
    return mode
