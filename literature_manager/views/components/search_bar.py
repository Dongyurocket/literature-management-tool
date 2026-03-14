from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget


class SearchBar(QWidget):
    searchRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.line_edit = QLineEdit(self)
        self.line_edit.setPlaceholderText("搜索标题、作者、关键词、摘要")
        self.line_edit.returnPressed.connect(self._emit_search)
        layout.addWidget(self.line_edit, stretch=1)

        button = QPushButton("搜索", self)
        button.clicked.connect(self._emit_search)
        layout.addWidget(button)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def _emit_search(self) -> None:
        self.searchRequested.emit(self.text())
