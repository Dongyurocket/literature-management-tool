from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..table_columns import literature_column_by_key, normalize_literature_column_keys
from ..viewmodels import LiteratureTableRow


class LiteratureTableModel(QAbstractTableModel):
    BATCH_SIZE = 120

    def __init__(self, column_keys: list[str] | None = None) -> None:
        super().__init__()
        self._all_rows: list[LiteratureTableRow] = []
        self._visible_rows: list[LiteratureTableRow] = []
        self._column_keys = normalize_literature_column_keys(column_keys)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._visible_rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._column_keys)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._visible_rows):
            return None
        row = self._visible_rows[index.row()]
        column_key = self._column_key_at(index.column())
        if column_key is None:
            return None
        value = self._value_for_key(row, column_key)

        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return self._display_value(value, column_key)
        if role == Qt.ItemDataRole.UserRole:
            return row.literature_id
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(self._alignment_for_column(column_key))
        if role == Qt.ItemDataRole.ForegroundRole and column_key == "reading_status":
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
        if orientation == Qt.Orientation.Horizontal:
            column_key = self._column_key_at(section)
            if column_key is None:
                return None
            spec = literature_column_by_key(column_key)
            return spec.label if spec is not None else column_key
        return section + 1

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        column_key = self._column_key_at(column)
        if column_key is None:
            return
        reverse = order == Qt.SortOrder.DescendingOrder
        self.beginResetModel()
        self._all_rows.sort(key=lambda row: self._sort_key(row, column_key), reverse=reverse)
        self._visible_rows = []
        self._append_batch(initial=True)
        self.endResetModel()

    def set_rows(self, rows: list[LiteratureTableRow]) -> None:
        self.beginResetModel()
        self._all_rows = list(rows)
        self._visible_rows = []
        self._append_batch(initial=True)
        self.endResetModel()

    def set_column_keys(self, column_keys: list[str] | None) -> None:
        normalized = normalize_literature_column_keys(column_keys)
        if normalized == self._column_keys:
            return
        self.beginResetModel()
        self._column_keys = normalized
        self.endResetModel()

    def column_keys(self) -> list[str]:
        return list(self._column_keys)

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

    def _column_key_at(self, column: int) -> str | None:
        if 0 <= column < len(self._column_keys):
            return self._column_keys[column]
        return None

    def _alignment_for_column(self, column_key: str) -> Qt.AlignmentFlag:
        spec = literature_column_by_key(column_key)
        if spec is not None and spec.alignment == "center":
            return Qt.AlignmentFlag.AlignCenter
        return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    def _display_value(self, value: object, column_key: str) -> str:
        if column_key in {"attachment_count", "note_count"}:
            return str(int(value or 0))
        if column_key == "rating":
            rating = int(value or 0)
            return str(rating) if rating > 0 else ""
        return str(value or "")

    def _value_for_key(self, row: LiteratureTableRow, column_key: str) -> object:
        if column_key == "title":
            return row.title
        if column_key == "year":
            return row.year
        if column_key == "entry_type":
            return row.entry_type
        if column_key == "authors":
            return row.authors
        if column_key == "subject":
            return row.subject
        if column_key == "reading_status":
            return row.reading_status
        if column_key == "attachment_count":
            return row.attachment_count
        if column_key == "note_count":
            return row.note_count
        if column_key == "rating":
            return row.rating
        if column_key == "tags":
            return row.tags
        if column_key == "publication_title":
            return row.publication_title
        if column_key == "publisher":
            return row.publisher
        if column_key == "language":
            return row.language
        if column_key == "doi":
            return row.doi
        if column_key == "cite_key":
            return row.cite_key
        if column_key == "created_at":
            return row.created_at
        if column_key == "updated_at":
            return row.updated_at
        return row.title

    def _sort_key(self, row: LiteratureTableRow, column_key: str) -> Any:
        value = self._value_for_key(row, column_key)
        if column_key == "year":
            return int(value) if str(value).isdigit() else 0
        if column_key in {"attachment_count", "note_count", "rating"}:
            return int(value or 0)
        if column_key == "reading_status":
            order = {"在读": 0, "未开始": 1, "已读": 2, "搁置": 3}
            return order.get(str(value), 99)
        return str(value or "").lower()
