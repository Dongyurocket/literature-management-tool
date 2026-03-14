from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
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
from ...dedupe_service import COMPARE_FIELDS, build_merge_preview
from ...import_service import scan_import_sources
from ...utils import ENTRY_TYPE_LABELS, IMPORT_MODE_LABELS


def _entry_type_label(code: str) -> str:
    return ENTRY_TYPE_LABELS.get(code, code or "")


class MetadataPreviewDialog(QDialog):
    def __init__(self, payload: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("元数据预览")
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        text = QTextEdit(self)
        text.setReadOnly(True)
        provider = payload.get("source_provider", "未知来源")
        fallback_chain = " -> ".join(payload.get("metadata_fallback_chain", []))
        preview_lines = [
            f"来源：{provider}",
            f"标题：{payload.get('title', '')}",
            f"作者：{' / '.join(payload.get('authors', []))}",
            f"年份：{payload.get('year', '')}",
            f"刊名/书名：{payload.get('publication_title', '')}",
            f"出版社：{payload.get('publisher', '')}",
            f"DOI：{payload.get('doi', '')}",
            f"ISBN：{payload.get('isbn', '')}",
            f"URL：{payload.get('url', '')}",
            f"关键词：{payload.get('keywords', '')}",
        ]
        if fallback_chain:
            preview_lines.append(f"回退链路：{fallback_chain}")
        if payload.get("metadata_lookup_notice"):
            preview_lines.extend(["", f"提示：{payload['metadata_lookup_notice']}"])
        if payload.get("metadata_lookup_errors"):
            preview_lines.extend(["", "错误信息："])
            preview_lines.extend(f"- {item}" for item in payload.get("metadata_lookup_errors", []))
        preview_lines.extend(
            [
                "",
                "摘要 / 简介：",
                payload.get("abstract") or payload.get("summary", ""),
            ]
        )
        text.setPlainText("\n".join(preview_lines).strip())
        layout.addWidget(text)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("合并到当前文献")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class RenamePreviewDialog(QDialog):
    def __init__(self, previews: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF 重命名预览")
        self.resize(980, 560)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(previews), 3, self)
        self.table.setHorizontalHeaderLabels(["原路径", "新路径", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        for row, item in enumerate(previews):
            status = "待重命名" if item.get("changed") else "无需修改"
            self.table.setItem(row, 0, QTableWidgetItem(item["old_path"]))
            self.table.setItem(row, 1, QTableWidgetItem(item["new_path"]))
            self.table.setItem(row, 2, QTableWidgetItem(status))
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始重命名")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DuplicateDialog(QDialog):
    def __init__(self, groups: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.groups = groups
        self.result_payload: dict | None = None
        self.setWindowTitle("重复文献对比")
        self.resize(1180, 760)

        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("重复组"))
        self.group_list = QListWidget(self)
        self.group_list.currentRowChanged.connect(self._load_group)
        left.addWidget(self.group_list)
        layout.addLayout(left, stretch=2)

        right = QVBoxLayout()
        right.addWidget(QLabel("选择要保留的主记录"))

        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderLabels(["保留", "ID", "标题", "年份", "作者", "DOI / ISBN", "附件", "笔记"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_comparison)
        right.addWidget(self.table, stretch=1)

        right.addWidget(QLabel("字段冲突对比"))
        self.comparison_table = QTableWidget(0, 1, self)
        self.comparison_table.verticalHeader().setVisible(False)
        self.comparison_table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.comparison_table, stretch=1)

        right.addWidget(QLabel("合并结果预览"))
        self.preview_text = QTextEdit(self)
        self.preview_text.setReadOnly(True)
        right.addWidget(self.preview_text, stretch=1)

        buttons = QDialogButtonBox(parent=self)
        merge_button = buttons.addButton("合并所选重复项", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        merge_button.clicked.connect(self._submit)
        close_button.clicked.connect(self.reject)
        right.addWidget(buttons)

        layout.addLayout(right, stretch=5)

        for group in groups:
            first = group["items"][0]
            self.group_list.addItem(
                f"{group['reason']} | {first.get('title', '未命名')} 等 {len(group['items'])} 条"
            )
        if groups:
            self.group_list.setCurrentRow(0)

    def _selected_group(self) -> dict | None:
        index = self.group_list.currentRow()
        if 0 <= index < len(self.groups):
            return self.groups[index]
        return None

    def _load_group(self, index: int) -> None:
        if index < 0 or index >= len(self.groups):
            return
        group = self.groups[index]
        self.table.setRowCount(len(group["items"]))
        for row, item in enumerate(group["items"]):
            values = [
                "",
                str(item["id"]),
                item.get("title", ""),
                str(item.get("year") or ""),
                " / ".join(item.get("authors", [])),
                item.get("doi") or item.get("isbn") or "",
                str(len(item.get("attachments", []))),
                str(len(item.get("notes", []))),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 1:
                    cell.setData(Qt.ItemDataRole.UserRole, int(item["id"]))
                self.table.setItem(row, column, cell)
        if group["items"]:
            self.table.selectRow(0)
        self._update_comparison()

    def _update_comparison(self) -> None:
        group = self._selected_group()
        row = self.table.currentRow()
        if not group or row < 0:
            return
        primary_id_item = self.table.item(row, 1)
        if primary_id_item is None:
            return
        primary_id = int(primary_id_item.data(Qt.ItemDataRole.UserRole))
        primary = next((item for item in group["items"] if int(item["id"]) == primary_id), None)
        others = [item for item in group["items"] if int(item["id"]) != primary_id]
        if not primary:
            return

        for row_index in range(self.table.rowCount()):
            self.table.item(row_index, 0).setText("保留" if row_index == row else "")

        self.comparison_table.setRowCount(len(COMPARE_FIELDS))
        self.comparison_table.setColumnCount(len(group["items"]) + 1)
        headers = ["字段"] + [f"#{item['id']}" for item in group["items"]]
        self.comparison_table.setHorizontalHeaderLabels(headers)

        for row_index, (field, label) in enumerate(COMPARE_FIELDS):
            self.comparison_table.setItem(row_index, 0, QTableWidgetItem(label))
            raw_values: list[str] = []
            for column_index, item in enumerate(group["items"], start=1):
                value = item.get(field, "")
                if isinstance(value, list):
                    display = " / ".join(value)
                else:
                    display = str(value or "")
                raw_values.append(display)
                cell = QTableWidgetItem(display)
                if len({text for text in raw_values if text}) > 1:
                    cell.setBackground(QColor("#fff0c9"))
                self.comparison_table.setItem(row_index, column_index, cell)

        merged = build_merge_preview(primary, others)
        preview_lines = [
            f"保留记录：#{primary_id}",
            f"最终标题：{merged.get('title', '')}",
            f"作者：{' / '.join(merged.get('authors', []))}",
            f"年份：{merged.get('year') or ''}",
            f"DOI：{merged.get('doi') or ''}",
            f"ISBN：{merged.get('isbn') or ''}",
            f"主题：{merged.get('subject') or ''}",
            f"标签：{' / '.join(merged.get('tags', []))}",
            f"简介：{merged.get('summary') or ''}",
        ]
        self.preview_text.setPlainText("\n".join(preview_lines))

    def _submit(self) -> None:
        row = self.table.currentRow()
        group = self._selected_group()
        if row < 0 or group is None:
            QMessageBox.information(self, "重复文献对比", "请先选择要保留的主记录。")
            return
        primary_item = self.table.item(row, 1)
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
    def __init__(self, search_literatures: Callable[[str], list[dict[str, object]]], parent=None) -> None:
        super().__init__(parent)
        self._search_literatures = search_literatures
        self.selected_literature_id: int | None = None
        self.setWindowTitle("全文检索")
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.query_edit = QLineEdit(self)
        self.query_edit.setPlaceholderText("搜索标题、摘要、笔记、附件提取文本")
        self.query_edit.returnPressed.connect(self._search)
        toolbar.addWidget(self.query_edit, stretch=1)
        search_button = QPushButton("检索", self)
        search_button.clicked.connect(self._search)
        toolbar.addWidget(search_button)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["标题", "年份", "作者", "命中片段"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemDoubleClicked.connect(lambda _item: self._submit())
        layout.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(parent=self)
        locate_button = buttons.addButton("定位到文献", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        locate_button.clicked.connect(self._submit)
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _search(self) -> None:
        rows = self._search_literatures(self.query_edit.text().strip())
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
        self.export_template_key: str | None = None
        self.setWindowTitle("统计报表")
        self.resize(760, 620)

        lines = [
            f"文献总数：{stats['total_literatures']}",
            f"附件总数：{stats['total_attachments']}",
            f"笔记总数：{stats['total_notes']}",
            "",
            "按年份：",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_year"]) or "- 暂无数据",
            "",
            "按主题：",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_subject"]) or "- 暂无数据",
            "",
            "按阅读状态：",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_status"]) or "- 暂无数据",
        ]

        layout = QVBoxLayout(self)
        text = QTextEdit(self)
        text.setReadOnly(True)
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)

        buttons = QDialogButtonBox(parent=self)
        md_button = buttons.addButton("导出 Markdown", QDialogButtonBox.ButtonRole.ActionRole)
        json_button = buttons.addButton("导出 JSON", QDialogButtonBox.ButtonRole.ActionRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        md_button.clicked.connect(lambda: self._accept_export("markdown_stats"))
        json_button.clicked.connect(lambda: self._accept_export("json_stats"))
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_export(self, template_key: str) -> None:
        self.export_template_key = template_key
        self.accept()


class MaintenanceDialog(QDialog):
    refreshRequested = Signal()
    repairRequested = Signal()
    rebuildRequested = Signal()
    backupRequested = Signal()
    restoreRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("维护工具")
        self.resize(980, 620)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        refresh_button = QPushButton("刷新缺失项", self)
        refresh_button.clicked.connect(self.refreshRequested.emit)
        repair_button = QPushButton("按目录修复", self)
        repair_button.clicked.connect(self.repairRequested.emit)
        rebuild_button = QPushButton("重建索引", self)
        rebuild_button.clicked.connect(self.rebuildRequested.emit)
        backup_button = QPushButton("创建备份", self)
        backup_button.clicked.connect(self.backupRequested.emit)
        restore_button = QPushButton("恢复备份", self)
        restore_button.clicked.connect(self.restoreRequested.emit)
        for button in (refresh_button, repair_button, rebuild_button, backup_button, restore_button):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.status_label = QLabel("维护工具已就绪。")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["类型", "名称", "解析路径"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        for row_index, item in enumerate(rows):
            kind = "笔记" if item.get("kind") == "note" else "附件"
            name = item.get("title") or item.get("label") or f"ID {item['id']}"
            values = [kind, name, item.get("resolved_path", "")]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.status_label.setText(f"共发现 {len(rows)} 个缺失路径条目。")


class ImportCenterDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.items: list[dict] = []
        self.setWindowTitle("导入中心")
        self.resize(1120, 640)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        file_button = QPushButton("选择文件", self)
        file_button.clicked.connect(self._pick_files)
        folder_button = QPushButton("选择文件夹", self)
        folder_button.clicked.connect(self._pick_folder)
        self.import_mode_combo = QComboBox(self)
        for code, label in IMPORT_MODE_LABELS.items():
            self.import_mode_combo.addItem(label, code)
        self.import_mode_combo.setCurrentIndex(
            max(0, self.import_mode_combo.findData(settings.default_import_mode))
        )
        toolbar.addWidget(file_button)
        toolbar.addWidget(folder_button)
        toolbar.addWidget(QLabel("导入方式"))
        toolbar.addWidget(self.import_mode_combo)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.status_label = QLabel("请选择文件或文件夹进行扫描。")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["导入", "来源类型", "标题", "类型", "作者", "源路径"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(parent=self)
        import_button = buttons.addButton("导入选中项", QDialogButtonBox.ButtonRole.AcceptRole)
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
            "选择要导入的文件",
            filter="支持的文件 (*.pdf *.bib *.ris *.docx *.md *.markdown *.txt);;所有文件 (*.*)",
        )
        if files:
            self._load_sources(list(files))

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹")
        if folder:
            self._load_sources([folder])

    def _load_sources(self, paths: list[str]) -> None:
        self.items = scan_import_sources(paths, settings=self.settings)
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
                "note_record": "笔记文件",
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
        self.status_label.setText(f"共扫描到 {len(self.items)} 条可导入记录。")

    def _submit(self) -> None:
        if not self.items:
            QMessageBox.information(self, "导入中心", "当前没有可导入内容。")
            return
        self.accept()


class TemplateChoiceDialog(QDialog):
    def __init__(self, title: str, templates: dict[str, str], current_key: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.selected_key: str | None = None
        self.setWindowTitle(title)
        self.resize(420, 180)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.combo = QComboBox(self)
        for key, label in templates.items():
            self.combo.addItem(label, key)
        if current_key:
            self.combo.setCurrentIndex(max(0, self.combo.findData(current_key)))
        form.addRow("模板", self.combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _submit(self) -> None:
        self.selected_key = str(self.combo.currentData())
        self.accept()


class LibraryProfilesDialog(QDialog):
    def __init__(self, summaries: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.action_payload: dict | None = None
        self._summaries = summaries
        self.setWindowTitle("文库管理")
        self.resize(980, 520)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(summaries), 5, self)
        self.table.setHorizontalHeaderLabels(["当前", "名称", "状态", "文库目录", "数据库"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        for row, item in enumerate(summaries):
            values = [
                "是" if item.get("active") else "",
                item.get("name", ""),
                "已归档" if item.get("archived") else "使用中",
                item.get("library_root", ""),
                item.get("database_path", ""),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 1:
                    cell.setData(Qt.ItemDataRole.UserRole, item)
                self.table.setItem(row, column, cell)
        if summaries:
            self.table.selectRow(next((idx for idx, item in enumerate(summaries) if item.get("active")), 0))

        buttons = QDialogButtonBox(parent=self)
        new_button = buttons.addButton("新建文库", QDialogButtonBox.ButtonRole.ActionRole)
        switch_button = buttons.addButton("切换到所选", QDialogButtonBox.ButtonRole.ActionRole)
        archive_button = buttons.addButton("归档 / 恢复", QDialogButtonBox.ButtonRole.ActionRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        new_button.clicked.connect(self._request_create)
        switch_button.clicked.connect(self._request_switch)
        archive_button.clicked.connect(self._request_archive_toggle)
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _selected_summary(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)
        if item is None:
            return None
        payload = item.data(Qt.ItemDataRole.UserRole)
        return payload if isinstance(payload, dict) else None

    def _request_create(self) -> None:
        name, ok = QInputDialog.getText(self, "新建文库", "请输入文库名称：")
        if not ok or not name.strip():
            return
        self.action_payload = {"action": "create", "name": name.strip()}
        self.accept()

    def _request_switch(self) -> None:
        summary = self._selected_summary()
        if not summary:
            return
        self.action_payload = {"action": "switch", "name": summary["name"]}
        self.accept()

    def _request_archive_toggle(self) -> None:
        summary = self._selected_summary()
        if not summary:
            return
        self.action_payload = {
            "action": "archive",
            "name": summary["name"],
            "archived": not bool(summary.get("archived")),
        }
        self.accept()


class UpdateInfoDialog(QDialog):
    def __init__(self, release_info: dict, parent=None) -> None:
        super().__init__(parent)
        self.action_payload: dict | None = None
        self.setWindowTitle("检查更新")
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        status = "发现新版本" if release_info.get("is_update_available") else "当前已是最新版本"
        header = QLabel(
            f"{status}\n当前版本：{release_info.get('current_version', '')}\n"
            f"最新版本：{release_info.get('latest_version', '')}"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        text = QTextEdit(self)
        text.setReadOnly(True)
        body_lines = [
            f"发布名称：{release_info.get('release_name', '')}",
            f"发布时间：{release_info.get('published_at', '')}",
            f"发布页：{release_info.get('html_url', '')}",
            f"安装包：{release_info.get('asset_name', '') or '未找到'}",
        ]
        if release_info.get("update_lookup_notice"):
            body_lines.extend(["", f"提示：{release_info.get('update_lookup_notice', '')}"])
        body_lines.extend(
            [
                "",
                "Release 说明：",
                release_info.get("body", "") or "暂无说明。",
            ]
        )
        text.setPlainText("\n".join(body_lines))
        layout.addWidget(text)

        buttons = QDialogButtonBox(parent=self)
        open_button = buttons.addButton("打开发布页", QDialogButtonBox.ButtonRole.ActionRole)
        open_button.clicked.connect(self._open_release)
        if release_info.get("is_update_available") and release_info.get("asset_url"):
            download_button = buttons.addButton("下载更新包", QDialogButtonBox.ButtonRole.AcceptRole)
            download_button.clicked.connect(self._download)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _download(self) -> None:
        self.action_payload = {"action": "download"}
        self.accept()

    def _open_release(self) -> None:
        self.action_payload = {"action": "open_release"}
        self.accept()
