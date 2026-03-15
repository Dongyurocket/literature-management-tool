from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...table_columns import (
    DEFAULT_LITERATURE_COLUMN_KEYS,
    available_literature_columns,
    normalize_literature_column_keys,
)


class ColumnSettingsDialog(QDialog):
    def __init__(self, selected_keys: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("文献列表列设置")
        self.resize(420, 520)

        layout = QVBoxLayout(self)
        tip = QLabel("勾选表示显示该列，可通过上下移动调整列顺序。列宽可回到列表中直接拖动表头修改。", self)
        tip.setWordWrap(True)
        layout.addWidget(tip)

        content = QHBoxLayout()
        self.column_list = QListWidget(self)
        self.column_list.setAlternatingRowColors(True)
        content.addWidget(self.column_list, stretch=1)

        side = QVBoxLayout()
        self.move_up_button = QPushButton("上移", self)
        self.move_up_button.clicked.connect(lambda: self._move_current(-1))
        side.addWidget(self.move_up_button)

        self.move_down_button = QPushButton("下移", self)
        self.move_down_button.clicked.connect(lambda: self._move_current(1))
        side.addWidget(self.move_down_button)

        self.select_all_button = QPushButton("全部显示", self)
        self.select_all_button.clicked.connect(self._select_all)
        side.addWidget(self.select_all_button)

        self.default_button = QPushButton("恢复默认", self)
        self.default_button.clicked.connect(self._restore_defaults)
        side.addWidget(self.default_button)

        side.addStretch(1)
        content.addLayout(side)
        layout.addLayout(content)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._accept_with_validation)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_items(selected_keys)

    def selected_column_keys(self) -> list[str]:
        keys: list[str] = []
        for index in range(self.column_list.count()):
            item = self.column_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return normalize_literature_column_keys(keys)

    def _load_items(self, selected_keys: list[str]) -> None:
        ordered_selected = normalize_literature_column_keys(selected_keys)
        remaining = [
            spec.key
            for spec in available_literature_columns()
            if spec.key not in ordered_selected
        ]
        ordered_keys = ordered_selected + remaining
        labels = {spec.key: spec.label for spec in available_literature_columns()}
        selected_set = set(ordered_selected)

        self.column_list.clear()
        for key in ordered_keys:
            item = QListWidgetItem(labels.get(key, key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked if key in selected_set else Qt.CheckState.Unchecked)
            self.column_list.addItem(item)
        if self.column_list.count() > 0:
            self.column_list.setCurrentRow(0)

    def _move_current(self, delta: int) -> None:
        row = self.column_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.column_list.count():
            return
        item = self.column_list.takeItem(row)
        self.column_list.insertItem(target, item)
        self.column_list.setCurrentRow(target)

    def _select_all(self) -> None:
        for index in range(self.column_list.count()):
            self.column_list.item(index).setCheckState(Qt.CheckState.Checked)

    def _restore_defaults(self) -> None:
        self._load_items(list(DEFAULT_LITERATURE_COLUMN_KEYS))

    def _accept_with_validation(self) -> None:
        if not self.selected_column_keys():
            QMessageBox.warning(self, "列设置", "请至少保留一列。")
            return
        self.accept()
