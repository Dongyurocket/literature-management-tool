from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..viewmodels import LiteratureTableRow


class LiteratureTableModel(QAbstractTableModel):
    HEADERS = ["标题", "年份", "类型", "作者", "主题", "阅读状态", "附件数"]
    BATCH_SIZE = 120

    def __init__(self) -> None:
        super().__init__()
        self._all_rows: list[LiteratureTableRow] = []
        self._visible_rows: list[LiteratureTableRow] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._visible_rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._visible_rows):
            return None
        row = self._visible_rows[index.row()]
        value = self._value_for_column(row, index.column())

        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return value
        if role == Qt.ItemDataRole.UserRole:
            return row.literature_id
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in {1, 6}:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 5:
            palette = {
                "未开始": QColor("#8b5a2b"),
                "在读": QColor("#2d8f6f"),
                "已读": QColor("#0f6cbd"),
                "搁置": QColor("#8b6b00"),
            }
            return palette.get(str(value), QColor("#66758a"))
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return section + 1

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        reverse = order == Qt.SortOrder.DescendingOrder
        self.beginResetModel()
        self._all_rows.sort(key=lambda row: self._sort_key(row, column), reverse=reverse)
        self._visible_rows = []
        self._append_batch(initial=True)
        self.endResetModel()

    def set_rows(self, rows: list[LiteratureTableRow]) -> None:
        self.beginResetModel()
        self._all_rows = list(rows)
        self._visible_rows = []
        self._append_batch(initial=True)
        self.endResetModel()

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        if parent.isValid():
            return False
        return len(self._visible_rows) < len(self._all_rows)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        if parent.isValid() or not self.canFetchMore(parent):
            return
        start = len(self._visible_rows)
        remaining = min(self.BATCH_SIZE, len(self._all_rows) - start)
        if remaining <= 0:
            return
        self.beginInsertRows(QModelIndex(), start, start + remaining - 1)
        self._visible_rows.extend(self._all_rows[start : start + remaining])
        self.endInsertRows()

    def append_more_if_needed(self) -> None:
        if not self.canFetchMore():
            return
        start = len(self._visible_rows)
        remaining = min(self.BATCH_SIZE, len(self._all_rows) - start)
        if remaining <= 0:
            return
        self.beginInsertRows(QModelIndex(), start, start + remaining - 1)
        self._visible_rows.extend(self._all_rows[start : start + remaining])
        self.endInsertRows()

    def literature_id_at(self, row_index: int) -> int | None:
        if 0 <= row_index < len(self._visible_rows):
            return self._visible_rows[row_index].literature_id
        return None

    def row_index_for_literature(self, literature_id: int) -> int | None:
        for index, row in enumerate(self._visible_rows):
            if row.literature_id == literature_id:
                return index
        return None

    def total_count(self) -> int:
        return len(self._all_rows)

    def _append_batch(self, *, initial: bool = False) -> None:
        if initial:
            self._visible_rows = self._all_rows[: self.BATCH_SIZE]

    def _value_for_column(self, row: LiteratureTableRow, column: int) -> str:
        values = [
            row.title,
            row.year,
            row.entry_type,
            row.authors,
            row.subject,
            row.reading_status,
            str(row.attachment_count),
        ]
        return values[column]

    def _sort_key(self, row: LiteratureTableRow, column: int) -> Any:
        if column == 0:
            return row.title.lower()
        if column == 1:
            return int(row.year) if str(row.year).isdigit() else 0
        if column == 2:
            return row.entry_type.lower()
        if column == 3:
            return row.authors.lower()
        if column == 4:
            return row.subject.lower()
        if column == 5:
            order = {"在读": 0, "未开始": 1, "已读": 2, "搁置": 3}
            return order.get(row.reading_status, 99)
        if column == 6:
            return row.attachment_count
        return row.title.lower()
