from __future__ import annotations

import sys

from .config import APP_DISPLAY_NAME, SettingsStore
from .controllers import LibraryController
from .viewmodels import MainWindowViewModel


def main() -> int:
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        raise RuntimeError("未安装 PySide6，请先运行 `python -m pip install .`。") from exc
    from .views import QtMainWindow
    from .views.theme import apply_theme

    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    settings_store = SettingsStore()
    settings = settings_store.load()
    controller = LibraryController(settings_store, settings)
    apply_theme(app, settings.ui_theme)

    app.aboutToQuit.connect(controller.close)

    window = QtMainWindow(MainWindowViewModel(controller))
    window.show()
    return app.exec()
