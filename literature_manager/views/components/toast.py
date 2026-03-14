from __future__ import annotations

from collections import deque

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class ToastOverlay(QFrame):
    COLORS = {
        "info": ("rgba(15, 108, 189, 0.96)", "#ffffff"),
        "success": ("rgba(45, 143, 111, 0.96)", "#ffffff"),
        "warning": ("rgba(194, 124, 44, 0.96)", "#ffffff"),
        "error": ("rgba(176, 59, 59, 0.96)", "#ffffff"),
    }

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._queue: deque[tuple[str, str, int]] = deque()
        self._showing = False
        self.setVisible(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 12, 16, 12)

        self._title = QLabel(self)
        self._title.setStyleSheet("font-size: 12px; font-weight: 700; background: transparent;")
        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setStyleSheet("font-size: 11px; background: transparent;")
        self._layout.addWidget(self._title)
        self._layout.addWidget(self._message)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_next)

    def push(self, title: str, message: str, *, level: str = "info", duration_ms: int = 3200) -> None:
        self._queue.append((title, message, duration_ms if duration_ms > 0 else 3200, level))
        if not self._showing:
            self._show_next()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(420, max(280, parent.width() - 48))
        self.setFixedWidth(width)
        self.adjustSize()
        x = parent.width() - self.width() - 24
        y = parent.height() - self.height() - 24
        self.move(max(12, x), max(12, y))

    def _show_next(self) -> None:
        if not self._queue:
            self._showing = False
            self.hide()
            return
        self._showing = True
        title, message, duration_ms, level = self._queue.popleft()
        background, foreground = self.COLORS.get(level, self.COLORS["info"])
        self.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 16px;"
        )
        self._title.setText(title)
        self._message.setText(message)
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)
