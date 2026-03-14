from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...config import AppSettings
from ...controllers import LibraryController
from ...import_service import scan_import_sources
from ...utils import ENTRY_TYPE_LABELS, IMPORT_MODE_LABELS


def _entry_type_label(code: str) -> str:
    return ENTRY_TYPE_LABELS.get(code, code or "")


class MetadataPreviewDialog(QDialog):
    def __init__(self, payload: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Metadata Preview")
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        text = QTextEdit(self)
        text.setReadOnly(True)
        preview_lines = [
            f"Title: {payload.get('title', '')}",
            f"Authors: {' / '.join(payload.get('authors', []))}",
            f"Year: {payload.get('year', '')}",
            f"Publication: {payload.get('publication_title', '')}",
            f"Publisher: {payload.get('publisher', '')}",
            f"DOI: {payload.get('doi', '')}",
            f"ISBN: {payload.get('isbn', '')}",
            f"URL: {payload.get('url', '')}",
            f"Keywords: {payload.get('keywords', '')}",
            "",
            "Abstract / Summary:",
            payload.get("abstract") or payload.get("summary", ""),
        ]
        text.setPlainText("\n".join(preview_lines).strip())
        layout.addWidget(text)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class RenamePreviewDialog(QDialog):
    def __init__(self, previews: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF Rename Preview")
        self.resize(980, 560)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(previews), 3, self)
        self.table.setHorizontalHeaderLabels(["Old Path", "New Path", "Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        for row, item in enumerate(previews):
            status = "Pending" if item.get("changed") else "No Change"
            self.table.setItem(row, 0, QTableWidgetItem(item["old_path"]))
            self.table.setItem(row, 1, QTableWidgetItem(item["new_path"]))
            self.table.setItem(row, 2, QTableWidgetItem(status))
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DuplicateDialog(QDialog):
    def __init__(self, groups: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.groups = groups
        self.result_payload: dict | None = None
        self.setWindowTitle("Duplicate Detection")
        self.resize(980, 620)

        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("Duplicate Groups"))
        self.group_list = QListWidget(self)
        self.group_list.currentRowChanged.connect(self._load_group)
        left.addWidget(self.group_list)
        layout.addLayout(left, stretch=2)

        right = QVBoxLayout()
        right.addWidget(QLabel("Select the primary literature record to keep"))
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["ID", "Title", "Year", "Authors"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        right.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(parent=self)
        merge_button = buttons.addButton("Merge", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        merge_button.clicked.connect(self._submit)
        close_button.clicked.connect(self.reject)
        right.addWidget(buttons)

        layout.addLayout(right, stretch=4)

        for group in groups:
            self.group_list.addItem(
                f"{group['reason']} | {group['items'][0]['title']} 等 {len(group['items'])} 条"
            )
        if groups:
            self.group_list.setCurrentRow(0)

    def _load_group(self, index: int) -> None:
        if index < 0 or index >= len(self.groups):
            return
        group = self.groups[index]
        self.table.setRowCount(len(group["items"]))
        for row, item in enumerate(group["items"]):
            values = [
                str(item["id"]),
                item["title"],
                str(item.get("year") or ""),
                " / ".join(item.get("authors", [])),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, int(item["id"]))
                self.table.setItem(row, column, cell)
        if group["items"]:
            self.table.selectRow(0)

    def _submit(self) -> None:
        row = self.table.currentRow()
        group_index = self.group_list.currentRow()
        if row < 0 or group_index < 0:
            QMessageBox.information(self, "Duplicate Detection", "Select a primary record first.")
            return
        group = self.groups[group_index]
        primary_item = self.table.item(row, 0)
        if primary_item is None:
            return
        primary_id = int(primary_item.data(Qt.ItemDataRole.UserRole))
        merged_ids = [int(item["id"]) for item in group["items"] if int(item["id"]) != primary_id]
        self.result_payload = {
            "primary_id": primary_id,
            "merged_ids": merged_ids,
            "reason": group["reason"],
        }
        self.accept()


class SearchDialog(QDialog):
    def __init__(self, controller: LibraryController, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.selected_literature_id: int | None = None
        self.setWindowTitle("Full-text Search")
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.query_edit = QLineEdit(self)
        self.query_edit.returnPressed.connect(self._search)
        toolbar.addWidget(self.query_edit, stretch=1)
        search_button = QPushButton("Search", self)
        search_button.clicked.connect(self._search)
        toolbar.addWidget(search_button)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Title", "Year", "Authors", "Hit"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemDoubleClicked.connect(lambda _item: self._submit())
        layout.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(parent=self)
        locate_button = buttons.addButton("Locate Record", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        locate_button.clicked.connect(self._submit)
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _search(self) -> None:
        rows = self.controller.search_literatures(self.query_edit.text().strip())
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("title", ""),
                str(row.get("year") or ""),
                row.get("authors_display", ""),
                row.get("summary_hit") or row.get("notes_hit") or row.get("attachment_hit") or "",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
                self.table.setItem(row_index, column, cell)
        if rows:
            self.table.selectRow(0)

    def _submit(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        self.selected_literature_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.accept()


class StatisticsDialog(QDialog):
    def __init__(self, stats: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Statistics")
        self.resize(760, 620)

        lines = [
            f"Literature Count: {stats['total_literatures']}",
            f"Attachment Count: {stats['total_attachments']}",
            f"Note Count: {stats['total_notes']}",
            "",
            "By Year:",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_year"]) or "- No Data",
            "",
            "By Subject:",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_subject"]) or "- No Data",
            "",
            "By Reading Status:",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_status"]) or "- No Data",
        ]

        layout = QVBoxLayout(self)
        text = QTextEdit(self)
        text.setReadOnly(True)
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MaintenanceDialog(QDialog):
    refreshRequested = Signal()
    repairRequested = Signal()
    rebuildRequested = Signal()
    backupRequested = Signal()
    restoreRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Maintenance Tools")
        self.resize(980, 620)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        refresh_button = QPushButton("Refresh Missing", self)
        refresh_button.clicked.connect(self.refreshRequested.emit)
        repair_button = QPushButton("Repair by Folder", self)
        repair_button.clicked.connect(self.repairRequested.emit)
        rebuild_button = QPushButton("Rebuild Index", self)
        rebuild_button.clicked.connect(self.rebuildRequested.emit)
        backup_button = QPushButton("Create Backup", self)
        backup_button.clicked.connect(self.backupRequested.emit)
        restore_button = QPushButton("Restore Backup", self)
        restore_button.clicked.connect(self.restoreRequested.emit)
        for button in (refresh_button, repair_button, rebuild_button, backup_button, restore_button):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.status_label = QLabel("Maintenance tools are ready.")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Type", "Name", "Resolved Path"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        for row_index, item in enumerate(rows):
            kind = "Note" if item.get("kind") == "note" else "Attachment"
            name = item.get("title") or item.get("label") or f"ID {item['id']}"
            values = [kind, name, item.get("resolved_path", "")]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.status_label.setText(f"Found {len(rows)} missing path item(s).")


class ImportCenterDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.items: list[dict] = []
        self.setWindowTitle("Import Center")
        self.resize(1120, 640)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        file_button = QPushButton("Choose Files", self)
        file_button.clicked.connect(self._pick_files)
        folder_button = QPushButton("Choose Folder", self)
        folder_button.clicked.connect(self._pick_folder)
        self.import_mode_combo = QComboBox(self)
        for code, label in IMPORT_MODE_LABELS.items():
            self.import_mode_combo.addItem(label, code)
        self.import_mode_combo.setCurrentIndex(
            max(0, self.import_mode_combo.findData(settings.default_import_mode))
        )
        toolbar.addWidget(file_button)
        toolbar.addWidget(folder_button)
        toolbar.addWidget(QLabel("Import Mode"))
        toolbar.addWidget(self.import_mode_combo)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.status_label = QLabel("Choose files or a folder to scan.")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["Import", "Source Type", "Title", "Entry Type", "Authors", "Source Path"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(parent=self)
        import_button = buttons.addButton("Import Selected", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        import_button.clicked.connect(self._submit)
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self) -> dict[str, object]:
        for row, item in enumerate(self.items):
            container = self.table.cellWidget(row, 0)
            selector = container.findChild(QCheckBox) if container is not None else None
            item["selected"] = bool(selector.isChecked()) if selector is not None else True
        return {
            "items": self.items,
            "import_mode": str(self.import_mode_combo.currentData()),
        }

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose files to import",
            filter="Supported files (*.pdf *.bib *.ris *.docx *.md *.markdown *.txt);;All files (*.*)",
        )
        if files:
            self._load_sources(list(files))

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose folder to scan")
        if folder:
            self._load_sources([folder])

    def _load_sources(self, paths: list[str]) -> None:
        self.items = scan_import_sources(paths)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            checkbox = QWidget(self.table)
            checkbox_layout = QHBoxLayout(checkbox)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            selector = QCheckBox(checkbox)
            selector.setChecked(True)
            checkbox_layout.addWidget(selector)
            checkbox_layout.addStretch(1)
            self.table.setCellWidget(row, 0, checkbox)

            kind_label = {
                "reference_record": "Bib/RIS",
                "file_record": "PDF",
                "note_record": "Note File",
            }.get(item["kind"], item["kind"])
            values = [
                kind_label,
                item["display_title"],
                _entry_type_label(item.get("entry_type", "")),
                " / ".join(item.get("authors", [])),
                item["source_path"],
            ]
            for column, value in enumerate(values, start=1):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.status_label.setText(f"Scanned {len(self.items)} importable item(s).")

    def _submit(self) -> None:
        if not self.items:
            QMessageBox.information(self, "Import Center", "No importable items are available.")
            return
        self.accept()
