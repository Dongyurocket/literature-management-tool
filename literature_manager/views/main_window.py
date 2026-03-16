from __future__ import annotations

import logging
import time
import traceback
import webbrowser
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QColor, QDragEnterEvent, QDropEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableView,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import APP_DISPLAY_NAME
from ..desktop import open_parent_folder, open_path
from ..metadata_fields import metadata_field_label, metadata_field_set, prune_metadata_payload
from ..models import LiteratureTableModel
from ..table_columns import literature_column_by_key
from ..utils import (
    ENTRY_TYPE_LABELS,
    READING_STATUSES,
    ROLE_LABELS,
    detect_note_format,
    join_csv,
    load_note_preview,
    split_csv,
)
from ..viewmodels import MainWindowViewModel, NavigationItem, StatCard
from .async_worker import AsyncWorker, WorkerError
from .components import SearchBar
from .components import ToastOverlay
from .dialogs import (
    AttachmentDialog,
    ColumnSettingsDialog,
    DuplicateDialog,
    ImportCenterDialog,
    LibraryProfilesDialog,
    MaintenanceDialog,
    MetadataPreviewDialog,
    RenamePreviewDialog,
    SearchDialog,
    SettingsDialog,
    StatisticsDialog,
    TemplateChoiceDialog,
    UpdateInfoDialog,
)
from .theme import apply_theme

_logger = logging.getLogger(__name__)

TOAST_SELECT_LITERATURE_MESSAGE = "请先在文献列表中选择一条或多条记录。"
TOAST_EXPORT_SUCCESS_TEMPLATE = "已导出 {count} 条记录到 `{path}`。"
TOAST_FILE_SAVED_TEMPLATE = "文件已保存到 `{path}`。"


class DropOverlay(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setVisible(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            "background: rgba(15, 108, 189, 0.12);"
            "border: 2px dashed rgba(15, 108, 189, 0.8);"
            "border-radius: 28px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        label = QLabel("拖入 PDF、BibTeX、RIS、Markdown 或 DOCX 文件即可导入")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "font-size: 22px; font-weight: 700; color: #0f6cbd; background: transparent;"
        )
        hint = QLabel("也支持拖入文件夹，系统会按当前默认导入方式进行处理。")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 13px; color: #21507a; background: transparent;")
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addWidget(hint)
        layout.addStretch(1)


class QtMainWindow(QMainWindow):
    def __init__(self, viewmodel: MainWindowViewModel) -> None:
        super().__init__()
        self.viewmodel = viewmodel
        self._table_model = LiteratureTableModel()
        self._current_literature_id: int | None = None
        self._current_note_id: int | None = None
        self._current_attachment_id: int | None = None
        self._current_note_is_file = False
        self._active_filters: dict[str, object] = {}
        self._navigation_items: dict[str, QTreeWidgetItem] = {}
        self._loading_metadata = False
        self._metadata_snapshot: dict[str, object] | None = None
        self._loading_notes = False
        self._loading_attachments = False
        self._maintenance_dialog: MaintenanceDialog | None = None
        self._active_workers: list[AsyncWorker] = []
        self._active_task_labels: set[str] = set()
        self._thread_pool = QThreadPool.globalInstance()
        self._busy_tasks: list[tuple[int, str]] = []
        self._busy_task_seq = 0
        self._busy_task_started_at: dict[int, float] = {}
        self._busy_state_guard_timer = QTimer(self)
        self._busy_state_guard_timer.setInterval(1500)
        self._busy_state_guard_timer.timeout.connect(self._recover_stale_busy_state)
        self._busy_state_guard_timer.start()

        self._metadata_save_timer = QTimer(self)
        self._metadata_save_timer.setSingleShot(True)
        self._metadata_save_timer.timeout.connect(self._save_metadata_changes)
        self._updating_metadata_autosave_controls = False

        self._table_layout_save_timer = QTimer(self)
        self._table_layout_save_timer.setInterval(500)
        self._table_layout_save_timer.setSingleShot(True)
        self._table_layout_save_timer.timeout.connect(self._persist_current_table_layout)
        self._applying_table_layout = False

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1640, 980)
        self.setAcceptDrops(True)

        self._build_ui()
        self._apply_metadata_autosave_preferences()
        self._apply_table_preferences()
        self._bind_shortcuts()
        self._apply_theme(self.viewmodel.settings.ui_theme)
        self._load_navigation("all")
        self._refresh_stats()
        self._refresh_table()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._busy_state_guard_timer.stop()
        if self._table_layout_save_timer.isActive():
            self._table_layout_save_timer.stop()
        self._flush_pending_metadata_changes()
        self._persist_current_table_layout()
        for worker in self._active_workers:
            worker.cancel()
        self._thread_pool.waitForDone(2000)
        if self._maintenance_dialog is not None:
            self._maintenance_dialog.close()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_drop_overlay") and self.centralWidget() is not None:
            self._drop_overlay.setGeometry(self.centralWidget().rect())
        if hasattr(self, "_toast_overlay"):
            self._toast_overlay.reposition()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._event_has_local_paths(event):
            event.acceptProposedAction()
            self._drop_overlay.setGeometry(self.centralWidget().rect())
            self._drop_overlay.show()
            self._drop_overlay.raise_()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drop_overlay.hide()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self._drop_overlay.hide()
        paths = self._event_local_paths(event)
        if paths:
            self._import_paths(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+Shift+F"), self, activated=self._open_search_center)
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_literature_list)

    def _focus_search(self) -> None:
        self.search_bar.line_edit.setFocus()
        self.search_bar.line_edit.selectAll()

    def _show_toast(self, title: str, message: str, *, level: str = "info", duration_ms: int = 3200) -> None:
        self._toast_overlay.push(title, message, level=level, duration_ms=duration_ms)

    def _show_select_literature_toast(self, title: str) -> None:
        self._show_toast(title, TOAST_SELECT_LITERATURE_MESSAGE, level="warning")

    def _show_file_saved_toast(self, title: str, path: str) -> None:
        self._show_toast(title, TOAST_FILE_SAVED_TEMPLATE.format(path=path), level="success")

    def _show_export_success_toast(self, count: int, path: str) -> None:
        self._show_toast(
            "导出完成",
            TOAST_EXPORT_SUCCESS_TEMPLATE.format(count=count, path=path),
            level="success",
        )

    def _update_settings(self, **changes):
        settings = replace(self.viewmodel.settings, **changes)
        self.viewmodel.save_settings(settings)
        return settings

    def _apply_metadata_autosave_preferences(self) -> None:
        interval_sec = max(1, int(self.viewmodel.settings.detail_autosave_interval_sec or 1))
        self._metadata_save_timer.setInterval(interval_sec * 1000)
        if not hasattr(self, "metadata_autosave_checkbox"):
            return
        self._updating_metadata_autosave_controls = True
        try:
            self.metadata_autosave_checkbox.setChecked(self.viewmodel.settings.detail_autosave_enabled)
            self.metadata_autosave_interval_spin.setValue(interval_sec)
            self.metadata_autosave_interval_spin.setEnabled(self.viewmodel.settings.detail_autosave_enabled)
        finally:
            self._updating_metadata_autosave_controls = False

    def _on_metadata_autosave_preference_changed(self) -> None:
        if self._updating_metadata_autosave_controls:
            return
        self._update_settings(
            detail_autosave_enabled=self.metadata_autosave_checkbox.isChecked(),
            detail_autosave_interval_sec=self.metadata_autosave_interval_spin.value(),
        )
        self._apply_metadata_autosave_preferences()
        if self._metadata_is_dirty():
            self._schedule_metadata_save()

    def _apply_table_preferences(self) -> None:
        self._table_model.set_column_keys(self.viewmodel.settings.list_columns)
        if hasattr(self, "table"):
            self._apply_table_column_layout()

    def _apply_table_column_layout(self) -> None:
        if not hasattr(self, "table"):
            return
        header = self.table.horizontalHeader()
        self._applying_table_layout = True
        header.blockSignals(True)
        try:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.setStretchLastSection(False)
            for index, key in enumerate(self._table_model.column_keys()):
                spec = literature_column_by_key(key)
                width = self.viewmodel.settings.list_column_widths.get(key)
                if width is None and spec is not None:
                    width = spec.default_width
                self.table.setColumnWidth(index, int(width or 120))
        finally:
            header.blockSignals(False)
            self._applying_table_layout = False

    def _capture_current_table_column_widths(self) -> dict[str, int]:
        widths = dict(self.viewmodel.settings.list_column_widths)
        if not hasattr(self, "table"):
            return widths
        for index, key in enumerate(self._table_model.column_keys()):
            widths[key] = max(self.table.columnWidth(index), 40)
        return widths

    def _save_table_preferences(
        self,
        *,
        column_keys: list[str] | None = None,
        column_widths: dict[str, int] | None = None,
    ) -> None:
        changes = {}
        if column_keys is not None:
            changes["list_columns"] = list(column_keys)
        if column_widths is not None:
            changes["list_column_widths"] = dict(column_widths)
        if changes:
            self._update_settings(**changes)

    def _persist_current_table_layout(self) -> None:
        if not hasattr(self, "table"):
            return
        self._save_table_preferences(column_widths=self._capture_current_table_column_widths())

    def _on_table_section_resized(self, _logical_index: int, _old_size: int, _new_size: int) -> None:
        if self._applying_table_layout:
            return
        self._table_layout_save_timer.start()

    def _flush_pending_metadata_changes(self) -> None:
        if self._metadata_save_timer.isActive():
            self._metadata_save_timer.stop()
        if self._loading_metadata or self._current_literature_id is None:
            return
        if self._metadata_is_dirty():
            self._save_metadata_changes()

    def _refresh_busy_state(self) -> None:
        if self._busy_tasks:
            message = self._busy_tasks[-1][1]
            self.busy_progress.setVisible(True)
            self.busy_label.setText(message)
            self.statusBar().showMessage(message)
            return
        self.busy_progress.setVisible(False)
        self.busy_label.setText("就绪")
        self.statusBar().clearMessage()
        self.statusBar().showMessage("工作区已就绪。", 2500)

    def _begin_busy_task(self, message: str) -> int:
        self._busy_task_seq += 1
        token = self._busy_task_seq
        self._busy_tasks.append((token, message or "正在处理…"))
        self._busy_task_started_at[token] = time.monotonic()
        self._refresh_busy_state()
        return token

    def _end_busy_task(self, token: int) -> None:
        self._busy_tasks = [item for item in self._busy_tasks if item[0] != token]
        self._busy_task_started_at.pop(token, None)
        self._refresh_busy_state()

    def _recover_stale_busy_state(self) -> None:
        if not self._busy_tasks:
            return
        if self._thread_pool.activeThreadCount() > 0:
            return
        now = time.monotonic()
        stale_tokens = [
            token
            for token, _message in self._busy_tasks
            if now - self._busy_task_started_at.get(token, now) >= 6.0
        ]
        if not stale_tokens:
            return
        stale_set = set(stale_tokens)
        stale_labels = {msg for tok, msg in self._busy_tasks if tok in stale_set}
        self._busy_tasks = [item for item in self._busy_tasks if item[0] not in stale_set]
        for token in stale_tokens:
            self._busy_task_started_at.pop(token, None)
        self._active_task_labels -= stale_labels
        self._refresh_busy_state()
        if not self._busy_tasks:
            self.statusBar().showMessage("后台任务状态已自动恢复。", 3000)

    def _run_ui_callback(self, callback, *args, error_title: str) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            _logger.error("UI callback failed (%s)", error_title, exc_info=True)
            self._show_toast(
                error_title,
                self._error_summary(traceback.format_exc()),
                level="error",
                duration_ms=5200,
            )

    def _refresh_header_subtitle(self) -> None:
        profile = self.viewmodel.current_library_profile()
        library_name = profile.get("name", "默认文献库")
        archive_state = "（已归档）" if profile.get("archived") else ""
        self.header_subtitle.setText(
            f"当前文库：{library_name}{archive_state}。支持拖拽导入、批量导出、全文检索、"
            "多元数据源回退、重复对比与 GitHub 更新。"
        )

    def _reload_primary_controller(self) -> None:
        settings = self.viewmodel.reload_settings_and_database()
        self._apply_theme(settings.ui_theme)
        self._refresh_header_subtitle()

    def _error_summary(self, error: WorkerError | str) -> str:
        if isinstance(error, WorkerError):
            return error.message.strip() or error.exception_type or "未知错误"
        parts = [line.strip() for line in error.splitlines() if line.strip()]
        return parts[-1] if parts else "未知错误"

    def _run_async_task(
        self,
        *,
        label: str,
        task,
        on_result=None,
        on_error=None,
        on_finished=None,
        success_toast: tuple[str, str] | None = None,
        error_title: str = "操作失败",
    ) -> None:
        if label in self._active_task_labels:
            self._show_toast("提示", "任务正在执行，请稍候…", level="warning")
            return

        task_token = self._begin_busy_task(label)
        self._active_task_labels.add(label)
        task_closed = False
        worker = AsyncWorker(task)
        self._active_workers.append(worker)

        def close_busy_once() -> None:
            nonlocal task_closed
            if task_closed:
                return
            task_closed = True
            self._active_task_labels.discard(label)
            self._end_busy_task(task_token)

        def handle_result(result) -> None:
            if worker.is_cancelled:
                close_busy_once()
                return
            close_busy_once()
            self._run_ui_callback(on_result, result, error_title=error_title)
            if success_toast is not None:
                self._show_toast(success_toast[0], success_toast[1], level="success")

        def handle_error(error: WorkerError | str) -> None:
            if worker.is_cancelled:
                close_busy_once()
                return
            close_busy_once()
            if on_error is not None:
                self._run_ui_callback(on_error, error, error_title=error_title)
                return
            self._show_toast(error_title, self._error_summary(error), level="error", duration_ms=5200)

        def handle_finished() -> None:
            try:
                if not worker.is_cancelled:
                    self._run_ui_callback(on_finished, error_title=error_title)
            finally:
                close_busy_once()
                if worker in self._active_workers:
                    self._active_workers.remove(worker)

        worker.signals.result.connect(handle_result)
        worker.signals.error.connect(handle_error)
        worker.signals.finished.connect(handle_finished)
        self._thread_pool.start(worker)

    def _run_controller_task(
        self,
        *,
        label: str,
        controller_task,
        on_result=None,
        reload_after: bool = False,
        refresh_after: bool = False,
        preserve_id: int | None = None,
        success_toast: tuple[str, str] | None = None,
        error_title: str = "操作失败",
    ) -> None:
        def task():
            controller = self.viewmodel.clone_controller(auto_rebuild_index=False)
            try:
                return controller_task(controller)
            finally:
                controller.close()

        def handle_result(result) -> None:
            if reload_after:
                self._reload_primary_controller()
            if refresh_after:
                self._refresh_after_library_change(
                    preserve_id=preserve_id,
                    navigation_key=self._current_navigation_key(),
                )
            if on_result is not None:
                on_result(result)

        self._run_async_task(
            label=label,
            task=task,
            on_result=handle_result,
            success_toast=success_toast,
            error_title=error_title,
        )

    def _build_ui(self) -> None:
        container = QWidget(self)
        container.setObjectName("rootWindow")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_stats_row())

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([280, 860, 500])
        layout.addWidget(splitter, stretch=1)

        self.setCentralWidget(container)

        self._drop_overlay = DropOverlay(container)
        self._drop_overlay.setGeometry(container.rect())
        self._toast_overlay = ToastOverlay(container)

        status = QStatusBar(self)
        self.busy_label = QLabel("就绪", self)
        self.busy_progress = QProgressBar(self)
        self.busy_progress.setMaximumWidth(140)
        self.busy_progress.setMaximum(0)
        self.busy_progress.setVisible(False)
        status.addPermanentWidget(self.busy_label)
        status.addPermanentWidget(self.busy_progress)
        status.showMessage("工作区已就绪。")
        self.setStatusBar(status)

    def _build_header(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("surfaceCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(18)

        hero = QVBoxLayout()
        hero.setSpacing(4)
        title = QLabel(f"{APP_DISPLAY_NAME} v{__version__}")
        title.setObjectName("heroTitle")
        self.header_subtitle = QLabel()
        self.header_subtitle.setObjectName("heroSubtitle")
        self.header_subtitle.setWordWrap(True)
        self._refresh_header_subtitle()
        hero.addWidget(title)
        hero.addWidget(self.header_subtitle)
        layout.addLayout(hero, stretch=3)

        self.search_bar = SearchBar(self)
        self.search_bar.setMinimumWidth(420)
        self.search_bar.searchRequested.connect(self._on_search_requested)
        layout.addWidget(self.search_bar, stretch=4)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        controls.addWidget(self.theme_combo)

        add_button = QPushButton("新增文献", self)
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._create_literature)
        controls.addWidget(add_button)

        import_button = QPushButton("快速导入", self)
        import_button.clicked.connect(self._import_files)
        controls.addWidget(import_button)

        template_button = QPushButton("模板导出", self)
        template_button.clicked.connect(self._export_selected_template)
        controls.addWidget(template_button)

        search_button = QPushButton("全文检索", self)
        search_button.clicked.connect(self._open_search_center)
        controls.addWidget(search_button)

        refresh_button = QPushButton("刷新列表", self)
        refresh_button.clicked.connect(self._refresh_literature_list)
        controls.addWidget(refresh_button)

        update_button = QPushButton("检查更新", self)
        update_button.clicked.connect(self._check_updates)
        controls.addWidget(update_button)

        delete_button = QPushButton("删除", self)
        delete_button.setObjectName("ghostButton")
        delete_button.clicked.connect(self._delete_current_literature)
        controls.addWidget(delete_button)

        settings_button = QPushButton("设置", self)
        settings_button.setObjectName("ghostButton")
        settings_button.clicked.connect(self._open_settings)
        controls.addWidget(settings_button)

        layout.addLayout(controls, stretch=3)
        return card

    def _build_stats_row(self) -> QWidget:
        card = QWidget(self)
        self.stats_layout = QHBoxLayout(card)
        self.stats_layout.setContentsMargins(0, 0, 0, 0)
        self.stats_layout.setSpacing(12)
        return card

    def _build_left_panel(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("导航")
        title.setObjectName("sectionTitle")
        helper = QLabel("左侧显示系统集合，以及根据当前文库自动生成的主题、年份与标签。")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(helper)

        self.navigation_tree = QTreeWidget(card)
        self.navigation_tree.setHeaderHidden(True)
        self.navigation_tree.itemSelectionChanged.connect(self._on_navigation_changed)
        layout.addWidget(self.navigation_tree, stretch=1)

        utility_title = QLabel("工具箱")
        utility_title.setObjectName("sectionTitle")
        layout.addWidget(utility_title)

        utility_grid = QGridLayout()
        utility_grid.setHorizontalSpacing(8)
        utility_grid.setVerticalSpacing(8)
        actions = [
            ("导入中心", self._open_import_center),
            ("全文检索", self._open_search_center),
            ("导出 Bib", self._export_selected_bib),
            ("导出 CSL", self._export_selected_csl),
            ("导出模板", self._export_selected_template),
            ("复制 GB/T", self._copy_gbt_reference),
            ("PDF 重命名", self._rename_pdfs),
            ("重复检测", self._open_dedupe_center),
            ("统计报表", self._show_statistics_dialog),
            ("文库管理", self._open_library_profiles),
            ("维护工具", self._open_maintenance_center),
        ]
        for index, (label, handler) in enumerate(actions):
            button = QPushButton(label, self)
            button.clicked.connect(handler)
            utility_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(utility_grid)
        return card

    def _build_center_panel(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("文献列表")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self.column_settings_button = QPushButton("列设置", self)
        self.column_settings_button.setObjectName("ghostButton")
        self.column_settings_button.clicked.connect(self._open_column_settings)
        header.addWidget(self.column_settings_button)
        header.addStretch(1)

        self.filter_pill = QLabel("全部文献")
        self.filter_pill.setObjectName("filterPill")
        header.addWidget(self.filter_pill)
        layout.addLayout(header)

        self.result_hint = QLabel("可搜索标题、作者、主题、关键词、摘要；若需检索笔记或附件全文，请使用“全文检索”。")
        self.result_hint.setObjectName("mutedLabel")
        layout.addWidget(self.result_hint)

        self.table = QTableView(card)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setModel(self._table_model)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.table.verticalScrollBar().valueChanged.connect(self._maybe_fetch_more_rows)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header_view.sectionResized.connect(self._on_table_section_resized)
        layout.addWidget(self.table, stretch=1)
        return card
    def _build_right_panel(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("详细信息")
        title.setObjectName("sectionTitle")
        helper = QLabel("在此编辑元数据、维护笔记与附件，并查看当前选中文献的详细信息。")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(helper)

        self.detail_title = QLabel("请选择一条文献记录")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 700; background: transparent;")
        layout.addWidget(self.detail_title)

        self.detail_subtitle = QLabel("当前未选中文献")
        self.detail_subtitle.setObjectName("mutedLabel")
        layout.addWidget(self.detail_subtitle)

        self.tabs = QTabWidget(card)
        self.tabs.addTab(self._build_metadata_tab(), "元数据")
        self.tabs.addTab(self._build_notes_tab(), "笔记")
        self.tabs.addTab(self._build_attachments_tab(), "附件")
        layout.addWidget(self.tabs, stretch=1)
        return card

    def _build_metadata_tab(self) -> QWidget:
        outer = QWidget(self)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(10)

        action_row = QHBoxLayout()
        self.lookup_metadata_button = QPushButton("抓取元数据", self)
        self.lookup_metadata_button.clicked.connect(self._lookup_metadata)
        self.save_now_button = QPushButton("立即保存", self)
        self.save_now_button.clicked.connect(self._save_metadata_changes)
        self.metadata_autosave_checkbox = QCheckBox("自动保存", self)
        self.metadata_autosave_checkbox.toggled.connect(self._on_metadata_autosave_preference_changed)
        self.metadata_autosave_interval_spin = QSpinBox(self)
        self.metadata_autosave_interval_spin.setRange(1, 300)
        self.metadata_autosave_interval_spin.setSuffix(" 秒")
        self.metadata_autosave_interval_spin.valueChanged.connect(self._on_metadata_autosave_preference_changed)
        self.metadata_save_label = QLabel("已保存")
        self.metadata_save_label.setObjectName("mutedLabel")
        action_row.addWidget(self.lookup_metadata_button)
        action_row.addWidget(self.save_now_button)
        action_row.addWidget(self.metadata_autosave_checkbox)
        action_row.addWidget(self.metadata_autosave_interval_spin)
        action_row.addStretch(1)
        action_row.addWidget(self.metadata_save_label)
        outer_layout.addLayout(action_row)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget(scroll)
        grid = QGridLayout(container)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(18)

        basic_form = QFormLayout()
        basic_form.setSpacing(10)
        publication_form = QFormLayout()
        publication_form.setSpacing(10)
        extra_form = QFormLayout()
        extra_form.setSpacing(10)
        self._metadata_labels: dict[str, QLabel] = {}
        self._metadata_field_widgets: dict[str, QWidget] = {}

        grid.addWidget(self._section_label("基础信息"), 0, 0)
        grid.addWidget(self._section_label("出版信息"), 0, 1)
        grid.addWidget(self._section_label("扩展信息"), 2, 0)

        basic_widget = QWidget(container)
        basic_widget.setLayout(basic_form)
        publication_widget = QWidget(container)
        publication_widget.setLayout(publication_form)
        extra_widget = QWidget(container)
        extra_widget.setLayout(extra_form)

        grid.addWidget(basic_widget, 1, 0)
        grid.addWidget(publication_widget, 1, 1)
        grid.addWidget(extra_widget, 3, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.entry_type_combo = QComboBox(self)
        for code, label in ENTRY_TYPE_LABELS.items():
            self.entry_type_combo.addItem(label, code)
        self.title_edit = QLineEdit(self)
        self.subtitle_edit = QLineEdit(self)
        self.short_title_edit = QLineEdit(self)
        self.translated_title_edit = QLineEdit(self)
        self.authors_edit = QLineEdit(self)
        self.translators_edit = QLineEdit(self)
        self.editors_edit = QLineEdit(self)
        self.year_edit = QLineEdit(self)
        self.month_edit = QLineEdit(self)
        self.day_edit = QLineEdit(self)
        self.subject_edit = QLineEdit(self)
        self.keywords_edit = QLineEdit(self)
        self.tags_edit = QLineEdit(self)
        self.reading_status_combo = QComboBox(self)
        for status in READING_STATUSES:
            self.reading_status_combo.addItem(status, status)
        self.rating_spin = QSpinBox(self)
        self.rating_spin.setRange(0, 5)

        self.publication_title_edit = QLineEdit(self)
        self.publisher_edit = QLineEdit(self)
        self.publication_place_edit = QLineEdit(self)
        self.school_edit = QLineEdit(self)
        self.institution_edit = QLineEdit(self)
        self.conference_name_edit = QLineEdit(self)
        self.conference_place_edit = QLineEdit(self)
        self.degree_edit = QLineEdit(self)
        self.edition_edit = QLineEdit(self)
        self.standard_number_edit = QLineEdit(self)
        self.patent_number_edit = QLineEdit(self)
        self.report_number_edit = QLineEdit(self)
        self.volume_edit = QLineEdit(self)
        self.issue_edit = QLineEdit(self)
        self.pages_edit = QLineEdit(self)
        self.doi_edit = QLineEdit(self)
        self.isbn_edit = QLineEdit(self)
        self.url_edit = QLineEdit(self)
        self.access_date_edit = QLineEdit(self)
        self.language_edit = QLineEdit(self)
        self.country_edit = QLineEdit(self)

        self.cite_key_edit = QLineEdit(self)
        self.summary_edit = QTextEdit(self)
        self.summary_edit.setFixedHeight(90)
        self.abstract_edit = QTextEdit(self)
        self.abstract_edit.setFixedHeight(120)
        self.remarks_edit = QTextEdit(self)
        self.remarks_edit.setFixedHeight(120)

        self._add_metadata_row(basic_form, "entry_type", self.entry_type_combo)
        self._add_metadata_row(basic_form, "title", self.title_edit)
        self._add_metadata_row(basic_form, "subtitle", self.subtitle_edit)
        self._add_metadata_row(basic_form, "short_title", self.short_title_edit)
        self._add_metadata_row(basic_form, "translated_title", self.translated_title_edit)
        self._add_metadata_row(basic_form, "authors", self.authors_edit)
        self._add_metadata_row(basic_form, "translators", self.translators_edit)
        self._add_metadata_row(basic_form, "editors", self.editors_edit)
        self._add_metadata_row(basic_form, "year", self.year_edit)
        self._add_metadata_row(basic_form, "month", self.month_edit)
        self._add_metadata_row(basic_form, "day", self.day_edit)
        self._add_metadata_row(basic_form, "subject", self.subject_edit)
        self._add_metadata_row(basic_form, "keywords", self.keywords_edit)
        self._add_metadata_row(basic_form, "tags", self.tags_edit)
        self._add_metadata_row(basic_form, "reading_status", self.reading_status_combo)
        self._add_metadata_row(basic_form, "rating", self.rating_spin)

        self._add_metadata_row(publication_form, "publication_title", self.publication_title_edit)
        self._add_metadata_row(publication_form, "publisher", self.publisher_edit)
        self._add_metadata_row(publication_form, "publication_place", self.publication_place_edit)
        self._add_metadata_row(publication_form, "school", self.school_edit)
        self._add_metadata_row(publication_form, "institution", self.institution_edit)
        self._add_metadata_row(publication_form, "conference_name", self.conference_name_edit)
        self._add_metadata_row(publication_form, "conference_place", self.conference_place_edit)
        self._add_metadata_row(publication_form, "degree", self.degree_edit)
        self._add_metadata_row(publication_form, "edition", self.edition_edit)
        self._add_metadata_row(publication_form, "standard_number", self.standard_number_edit)
        self._add_metadata_row(publication_form, "patent_number", self.patent_number_edit)
        self._add_metadata_row(publication_form, "report_number", self.report_number_edit)
        self._add_metadata_row(publication_form, "volume", self.volume_edit)
        self._add_metadata_row(publication_form, "issue", self.issue_edit)
        self._add_metadata_row(publication_form, "pages", self.pages_edit)
        self._add_metadata_row(publication_form, "doi", self.doi_edit)
        self._add_metadata_row(publication_form, "isbn", self.isbn_edit)
        self._add_metadata_row(publication_form, "url", self.url_edit)
        self._add_metadata_row(publication_form, "access_date", self.access_date_edit)
        self._add_metadata_row(publication_form, "language", self.language_edit)
        self._add_metadata_row(publication_form, "country", self.country_edit)

        self._add_metadata_row(extra_form, "cite_key", self.cite_key_edit)
        self._add_metadata_row(extra_form, "summary", self.summary_edit)
        self._add_metadata_row(extra_form, "abstract", self.abstract_edit)
        self._add_metadata_row(extra_form, "remarks", self.remarks_edit)

        self._connect_metadata_autosave()
        self.entry_type_combo.currentIndexChanged.connect(self._on_entry_type_changed)
        self._apply_entry_type_field_state(clear_hidden=False)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll, stretch=1)
        return outer

    def _add_metadata_row(self, form: QFormLayout, field: str, widget: QWidget) -> None:
        label = QLabel(metadata_field_label(field))
        form.addRow(label, widget)
        self._metadata_labels[field] = label
        self._metadata_field_widgets[field] = widget

    def _current_entry_type(self) -> str:
        return str(self.entry_type_combo.currentData() or "journal_article")

    def _clear_metadata_widget(self, widget: QWidget) -> None:
        if widget is self.entry_type_combo:
            return
        if isinstance(widget, QLineEdit):
            widget.clear()
            return
        if isinstance(widget, QTextEdit):
            widget.clear()
            return
        if isinstance(widget, QSpinBox):
            widget.setValue(0)
            return
        if isinstance(widget, QComboBox):
            widget.setCurrentIndex(0)

    def _apply_entry_type_field_state(self, *, clear_hidden: bool) -> None:
        entry_type = self._current_entry_type()
        allowed_fields = metadata_field_set(entry_type)
        for field, widget in self._metadata_field_widgets.items():
            label = self._metadata_labels[field]
            label.setText(metadata_field_label(field, entry_type))
            visible = field in allowed_fields
            label.setVisible(visible)
            widget.setVisible(visible)
            if clear_hidden and not visible:
                self._clear_metadata_widget(widget)

    def _on_entry_type_changed(self) -> None:
        self._apply_entry_type_field_state(clear_hidden=not self._loading_metadata)

    def _build_notes_tab(self) -> QWidget:
        card = QWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        button_row = QHBoxLayout()
        new_note_button = QPushButton("新建文本笔记", self)
        new_note_button.clicked.connect(self._create_text_note)
        link_note_button = QPushButton("关联笔记文件", self)
        link_note_button.clicked.connect(self._link_note_file)
        self.save_note_button = QPushButton("保存笔记", self)
        self.save_note_button.clicked.connect(self._save_note)
        self.delete_note_button = QPushButton("删除笔记", self)
        self.delete_note_button.clicked.connect(self._delete_selected_note)
        self.open_note_file_button = QPushButton("打开文件", self)
        self.open_note_file_button.clicked.connect(self._open_selected_note_file)

        for button in (
            new_note_button,
            link_note_button,
            self.save_note_button,
            self.delete_note_button,
            self.open_note_file_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.notes_list = QListWidget(self)
        self.notes_list.currentItemChanged.connect(self._on_note_selected)
        splitter.addWidget(self.notes_list)

        editor = QWidget(self)
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(10)

        top_row = QHBoxLayout()
        self.note_title_edit = QLineEdit(self)
        self.note_title_edit.setPlaceholderText("笔记标题")
        self.note_format_combo = QComboBox(self)
        self.note_format_combo.addItem("Markdown", "markdown")
        self.note_format_combo.addItem("纯文本", "text")
        top_row.addWidget(self.note_title_edit, stretch=1)
        top_row.addWidget(self.note_format_combo)
        editor_layout.addLayout(top_row)

        self.note_info_label = QLabel("可以创建文本笔记，也可以选中已有笔记继续编辑。")
        self.note_info_label.setObjectName("mutedLabel")
        self.note_info_label.setWordWrap(True)
        editor_layout.addWidget(self.note_info_label)

        self.note_body_edit = QTextEdit(self)
        self.note_body_edit.setPlaceholderText("输入 Markdown 或纯文本笔记内容")
        editor_layout.addWidget(self.note_body_edit, stretch=1)

        splitter.addWidget(editor)
        splitter.setSizes([180, 320])
        layout.addWidget(splitter, stretch=1)
        return card

    def _build_attachments_tab(self) -> QWidget:
        card = QWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        button_row = QHBoxLayout()
        add_button = QPushButton("添加附件", self)
        add_button.clicked.connect(self._add_attachments)
        self.open_attachment_button = QPushButton("打开", self)
        self.open_attachment_button.clicked.connect(self._open_selected_attachment)
        self.reveal_attachment_button = QPushButton("打开所在文件夹", self)
        self.reveal_attachment_button.clicked.connect(self._reveal_selected_attachment)
        self.delete_attachment_button = QPushButton("删除", self)
        self.delete_attachment_button.clicked.connect(self._delete_selected_attachment)
        for button in (
            add_button,
            self.open_attachment_button,
            self.reveal_attachment_button,
            self.delete_attachment_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.attachments_list = QListWidget(self)
        self.attachments_list.currentItemChanged.connect(self._on_attachment_selected)
        self.attachments_list.itemDoubleClicked.connect(self._on_attachment_double_clicked)
        delete_shortcut = QShortcut(QKeySequence("Delete"), self.attachments_list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self._delete_selected_attachment)
        layout.addWidget(self.attachments_list, stretch=1)

        self.attachments_info_label = QLabel("打开 PDF 时会优先使用设置中的阅读器，其他文件则使用系统默认程序。")
        self.attachments_info_label.setObjectName("mutedLabel")
        self.attachments_info_label.setWordWrap(True)
        layout.addWidget(self.attachments_info_label)
        return card
    def _refresh_stats(self) -> None:
        while self.stats_layout.count():
            item = self.stats_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for stat in self.viewmodel.quick_stats():
            self.stats_layout.addWidget(self._build_stat_card(stat), stretch=1)

    def _build_stat_card(self, stat: StatCard) -> QWidget:
        card = QFrame(self)
        card.setObjectName("statCard")
        card.setStyleSheet(f"QFrame#statCard {{ border-left: 5px solid {stat.accent}; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        title = QLabel(stat.label)
        title.setObjectName("mutedLabel")
        value = QLabel(stat.value)
        value.setStyleSheet(
            f"font-size: 26px; font-weight: 700; color: {stat.accent}; background: transparent;"
        )
        helper = QLabel(stat.helper_text)
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(value)
        layout.addWidget(helper)
        return card

    def _load_navigation(self, selection_key: str | None = None) -> None:
        self.navigation_tree.blockSignals(True)
        self.navigation_tree.clear()
        self._navigation_items.clear()
        first_leaf: QTreeWidgetItem | None = None
        current_key = selection_key or self._current_navigation_key() or "all"

        for section, items in self.viewmodel.navigation_sections().items():
            root = QTreeWidgetItem([section])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for item in items:
                child = QTreeWidgetItem([f"{item.label} ({item.count})"])
                child.setData(0, Qt.ItemDataRole.UserRole, item)
                if not item.enabled:
                    child.setForeground(0, QColor("#9aa8ba"))
                root.addChild(child)
                self._navigation_items[item.key] = child
                if first_leaf is None and item.enabled:
                    first_leaf = child
            self.navigation_tree.addTopLevelItem(root)
            root.setExpanded(True)

        target = self._navigation_items.get(current_key) or first_leaf
        if target is not None:
            self.navigation_tree.setCurrentItem(target)
            navigation = target.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(navigation, NavigationItem):
                self._active_filters = dict(navigation.filters)
        self.navigation_tree.blockSignals(False)

    def _refresh_table(self, search_text: str | None = None, preserve_id: int | None = None) -> None:
        self._flush_pending_metadata_changes()
        rows = self.viewmodel.list_rows(
            search=search_text if search_text is not None else self.search_bar.text(),
            filters=self._active_filters,
        )
        self._table_model.set_rows(rows)
        self.result_hint.setText(
            f"共匹配到 {self._table_model.total_count()} 条记录；列表会在滚动时分批加载。"
        )
        self.filter_pill.setText(self.viewmodel.filter_summary(self._active_filters))

        if preserve_id is not None and self._select_row_for_literature(preserve_id):
            return
        if rows:
            self.table.selectRow(0)
            self._show_detail(rows[0].literature_id)
        else:
            self._show_detail(None)

    def _select_row_for_literature(self, literature_id: int) -> bool:
        while self._table_model.row_index_for_literature(literature_id) is None and self._table_model.canFetchMore():
            self._table_model.append_more_if_needed()
        row_index = self._table_model.row_index_for_literature(literature_id)
        if row_index is None:
            return False
        self.table.selectRow(row_index)
        self._show_detail(literature_id)
        return True

    def _refresh_after_library_change(
        self,
        *,
        preserve_id: int | None = None,
        navigation_key: str | None = None,
    ) -> None:
        self._refresh_stats()
        self._load_navigation(navigation_key)
        self._refresh_table(preserve_id=preserve_id)
        self._refresh_header_subtitle()

    def _connect_metadata_autosave(self) -> None:
        widgets = [
            self.entry_type_combo,
            self.title_edit,
            self.subtitle_edit,
            self.translated_title_edit,
            self.authors_edit,
            self.translators_edit,
            self.editors_edit,
            self.year_edit,
            self.month_edit,
            self.day_edit,
            self.subject_edit,
            self.keywords_edit,
            self.tags_edit,
            self.reading_status_combo,
            self.publication_title_edit,
            self.publisher_edit,
            self.publication_place_edit,
            self.school_edit,
            self.institution_edit,
            self.conference_name_edit,
            self.conference_place_edit,
            self.degree_edit,
            self.edition_edit,
            self.standard_number_edit,
            self.patent_number_edit,
            self.report_number_edit,
            self.volume_edit,
            self.issue_edit,
            self.pages_edit,
            self.doi_edit,
            self.isbn_edit,
            self.url_edit,
            self.access_date_edit,
            self.language_edit,
            self.country_edit,
            self.cite_key_edit,
        ]
        for widget in widgets:
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._schedule_metadata_save)
            else:
                widget.textEdited.connect(self._schedule_metadata_save)
        self.rating_spin.valueChanged.connect(self._schedule_metadata_save)
        self.summary_edit.textChanged.connect(self._schedule_metadata_save)
        self.abstract_edit.textChanged.connect(self._schedule_metadata_save)
        self.remarks_edit.textChanged.connect(self._schedule_metadata_save)

    def _schedule_metadata_save(self) -> None:
        if self._loading_metadata or self._current_literature_id is None:
            return
        if not self._metadata_is_dirty():
            self.metadata_save_label.setText("已保存")
            self._metadata_save_timer.stop()
            return
        if not self.viewmodel.settings.detail_autosave_enabled:
            self.metadata_save_label.setText("未保存")
            self._metadata_save_timer.stop()
            return
        self.metadata_save_label.setText("即将自动保存…")
        self._metadata_save_timer.start()

    def _collect_metadata_payload(self) -> dict[str, object]:
        payload = {
            "entry_type": str(self.entry_type_combo.currentData()),
            "title": self.title_edit.text().strip(),
            "subtitle": self.subtitle_edit.text().strip(),
            "short_title": self.short_title_edit.text().strip(),
            "translated_title": self.translated_title_edit.text().strip(),
            "authors": split_csv(self.authors_edit.text().replace("\n", ",")),
            "translators": self.translators_edit.text().strip(),
            "editors": self.editors_edit.text().strip(),
            "year": int(self.year_edit.text()) if self.year_edit.text().strip().isdigit() else None,
            "month": self.month_edit.text().strip(),
            "day": self.day_edit.text().strip(),
            "subject": self.subject_edit.text().strip(),
            "keywords": self.keywords_edit.text().strip(),
            "tags": split_csv(self.tags_edit.text().replace("\n", ",")),
            "reading_status": str(self.reading_status_combo.currentData()),
            "rating": self.rating_spin.value() or None,
            "publication_title": self.publication_title_edit.text().strip(),
            "publisher": self.publisher_edit.text().strip(),
            "publication_place": self.publication_place_edit.text().strip(),
            "school": self.school_edit.text().strip(),
            "institution": self.institution_edit.text().strip(),
            "conference_name": self.conference_name_edit.text().strip(),
            "conference_place": self.conference_place_edit.text().strip(),
            "degree": self.degree_edit.text().strip(),
            "edition": self.edition_edit.text().strip(),
            "standard_number": self.standard_number_edit.text().strip(),
            "patent_number": self.patent_number_edit.text().strip(),
            "report_number": self.report_number_edit.text().strip(),
            "volume": self.volume_edit.text().strip(),
            "issue": self.issue_edit.text().strip(),
            "pages": self.pages_edit.text().strip(),
            "doi": self.doi_edit.text().strip(),
            "isbn": self.isbn_edit.text().strip(),
            "url": self.url_edit.text().strip(),
            "access_date": self.access_date_edit.text().strip(),
            "language": self.language_edit.text().strip(),
            "country": self.country_edit.text().strip(),
            "summary": self.summary_edit.toPlainText().strip(),
            "abstract": self.abstract_edit.toPlainText().strip(),
            "remarks": self.remarks_edit.toPlainText().strip(),
            "cite_key": self.cite_key_edit.text().strip(),
        }
        return prune_metadata_payload(payload, entry_type=payload.get("entry_type"))

    def _metadata_is_dirty(self) -> bool:
        if self._metadata_snapshot is None:
            return False
        current_payload = self.viewmodel.normalize_metadata_payload(self._collect_metadata_payload())
        return current_payload != self._metadata_snapshot

    def _save_metadata_changes(self) -> None:
        if self._loading_metadata or self._current_literature_id is None:
            return
        if not self._metadata_is_dirty():
            self.metadata_save_label.setText("已保存")
            return
        refreshed = self.viewmodel.save_metadata(
            self._current_literature_id,
            self._collect_metadata_payload(),
        )
        self._update_detail_header(refreshed)
        self._populate_metadata(refreshed)
        self.metadata_save_label.setText("已保存")
        self.statusBar().showMessage("元数据已保存。", 2500)

    def _create_literature(self) -> None:
        literature_id = self.viewmodel.create_new_literature()
        self.search_bar.line_edit.clear()
        self._refresh_after_library_change(preserve_id=literature_id, navigation_key="all")
        self._show_toast("已创建", "已新建一条文献记录，可立即编辑。", level="success")

    def _delete_current_literature(self) -> None:
        if self._current_literature_id is None:
            return
        detail = self.viewmodel.detail_payload(self._current_literature_id)
        answer = QMessageBox.question(
            self,
            "删除文献",
            f"确认删除“{detail.get('title', '未命名文献')}”及其关联的笔记 / 附件记录吗？\n"
            "原始文件不会被自动删除。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted_id = self._current_literature_id
        self.viewmodel.delete_literature(deleted_id)
        self._refresh_after_library_change(navigation_key=self._current_navigation_key())
        self._show_toast("已删除", f"文献 #{deleted_id} 已删除。", level="success")

    def _open_settings(self) -> None:
        current_workspace = self.viewmodel.workspace_dir()
        dialog = SettingsDialog(
            self.viewmodel.settings,
            workspace_dir=current_workspace,
            workspace_locked=self.viewmodel.is_workspace_locked(),
            parent=self,
        )
        if dialog.exec() == 0:
            return
        settings = dialog.value()
        target_workspace = dialog.workspace_dir()
        workspace_changed = Path(target_workspace).expanduser().resolve() != Path(current_workspace).expanduser().resolve()
        try:
            applied_settings = self.viewmodel.apply_settings(settings, workspace_dir=target_workspace)
        except ValueError as exc:
            QMessageBox.warning(self, "设置", str(exc))
            return
        self._apply_metadata_autosave_preferences()
        self._apply_table_preferences()
        self._apply_theme(applied_settings.ui_theme)
        self._refresh_after_library_change(
            preserve_id=self._current_literature_id,
            navigation_key=self._current_navigation_key(),
        )
        message = "已更新桌面偏好设置。"
        if workspace_changed:
            message = "已更新设置并切换到新的同步工作区。"
        self._show_toast("设置已保存", message, level="success")

    def _open_column_settings(self) -> None:
        self._persist_current_table_layout()
        dialog = ColumnSettingsDialog(self._table_model.column_keys(), self)
        if dialog.exec() == 0:
            return
        column_keys = dialog.selected_column_keys()
        column_widths = self._capture_current_table_column_widths()
        self._table_model.set_column_keys(column_keys)
        self._save_table_preferences(column_keys=column_keys, column_widths=column_widths)
        self._apply_table_column_layout()
        if self._current_literature_id is not None:
            self._select_row_for_literature(self._current_literature_id)
        elif self._table_model.rowCount() > 0:
            self.table.selectRow(0)
        self._show_toast("列设置已保存", "已更新文献列表的显示列和列宽配置。", level="success")

    def _import_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "导入文献文件",
            filter="支持的文件 (*.pdf *.bib *.ris *.docx *.md *.markdown *.txt);;所有文件 (*.*)",
        )
        if selected:
            self._import_paths(selected)

    def _import_paths(self, paths: list[str]) -> None:
        preserve_id = self._current_literature_id

        def task(controller):
            return controller.import_paths(paths)

        def handle_result(result) -> None:
            items, summary = result
            if not items:
                self._show_toast(
                    "未导入任何内容",
                    "在所选或拖拽路径中未发现受支持的文件。",
                    level="warning",
                )
                return
            self._show_toast(
                "导入完成",
                f"成功导入 {summary['imported']} 项，跳过 {summary['skipped']} 项。",
                level="success",
            )

        self._run_controller_task(
            label="正在后台导入文件…",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=preserve_id,
            error_title="导入失败",
        )

    def _export_selected_bib(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_select_literature_toast("导出 Bib")
            return
        initial_dir = self.viewmodel.settings.recent_export_dir or str(Path.cwd())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 BibTeX",
            str(Path(initial_dir) / "文献导出.bib"),
            filter="BibTeX (*.bib)",
        )
        if not path:
            return
        count = self.viewmodel.export_bib(literature_ids, path)
        self._show_export_success_toast(count, path)

    def _export_selected_template(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_select_literature_toast("模板导出")
            return
        dialog = TemplateChoiceDialog(
            "选择导出模板",
            self.viewmodel.list_export_templates(),
            current_key=self.viewmodel.settings.preferred_export_template,
            parent=self,
        )
        if dialog.exec() == 0 or not dialog.selected_key:
            return
        template_key = dialog.selected_key
        suffix = self.viewmodel.suggested_export_extension(template_key)
        initial_dir = self.viewmodel.settings.recent_export_dir or str(Path.cwd())
        default_name = f"文献模板导出{suffix}"
        filters = {
            ".md": "Markdown (*.md)",
            ".csv": "CSV (*.csv)",
            ".html": "HTML (*.html)",
            ".txt": "Text (*.txt)",
        }
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存模板导出",
            str(Path(initial_dir) / default_name),
            filter=filters.get(suffix, "所有文件 (*.*)"),
        )
        if not path:
            return
        export_path = self.viewmodel.export_template(literature_ids, template_key, path)
        self._show_file_saved_toast("模板导出完成", export_path)

    def _check_updates(self) -> None:
        def handle_result(release_info) -> None:
            dialog = UpdateInfoDialog(release_info, self)
            if dialog.exec() == 0 or not dialog.action_payload:
                return
            action = dialog.action_payload.get("action")
            if action == "open_release" and release_info.get("html_url"):
                webbrowser.open(str(release_info["html_url"]))
                return
            if action != "download":
                return
            target_dir = QFileDialog.getExistingDirectory(self, "选择更新包保存目录")
            if not target_dir:
                return

            def download_task(controller):
                return controller.download_update(release_info, target_dir)

            def handle_downloaded(file_path: str) -> None:
                self._show_toast("更新包已下载", f"安装包已保存到 `{file_path}`。", level="success")

            self._run_controller_task(
                label="正在下载更新包…",
                controller_task=download_task,
                on_result=handle_downloaded,
                error_title="下载更新失败",
            )

        self._run_controller_task(
            label="正在检查 GitHub 更新…",
            controller_task=lambda controller: controller.check_for_updates(),
            on_result=handle_result,
            error_title="检查更新失败",
        )

    def _refresh_literature_list(self) -> None:
        preserve_id = self._current_literature_id
        navigation_key = self._current_navigation_key()
        self._flush_pending_metadata_changes()

        def handle_result(_result) -> None:
            self._show_toast("列表已刷新", "文献列表与统计信息已更新。", level="info")

        self._run_controller_task(
            label="正在刷新文献列表…",
            controller_task=lambda controller: True,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=preserve_id,
            error_title="刷新列表失败",
        )

    def _open_library_profiles(self) -> None:
        dialog = LibraryProfilesDialog(self.viewmodel.list_library_profiles(), self)
        if dialog.exec() == 0 or not dialog.action_payload:
            return
        payload = dialog.action_payload
        action = payload.get("action")
        if action == "create":
            try:
                created = self.viewmodel.create_library_profile(str(payload["name"]), library_root=payload.get("library_root"))
            except ValueError as exc:
                QMessageBox.warning(self, "新建文库", str(exc))
                return
            self._show_toast("文库已创建", f"已创建文库“{created['name']}”。", level="success")
            self._open_library_profiles()
            return
        if action == "switch":
            self._flush_pending_metadata_changes()
            try:
                summary = self.viewmodel.switch_library_profile(str(payload["name"]))
            except ValueError as exc:
                QMessageBox.warning(self, "切换文库", str(exc))
                return
            self._refresh_after_library_change(navigation_key="all")
            self._show_toast("文库已切换", f"当前文库：{summary['name']}。", level="success")
            return
        if action == "archive":
            try:
                summary = self.viewmodel.set_library_archived(
                    str(payload["name"]),
                    bool(payload["archived"]),
                )
            except ValueError as exc:
                QMessageBox.warning(self, "归档文库", str(exc))
                return
            self._refresh_after_library_change(navigation_key="all")
            state = "已归档" if summary["archived"] else "已恢复"
            self._show_toast("文库状态已更新", f"文库“{summary['name']}”{state}。", level="success")
            self._open_library_profiles()
        if action == "delete":
            name = str(payload["name"])
            answer = QMessageBox.question(
                self,
                "删除文库",
                f"确定要删除文库“{name}”吗？\n\n选择 Yes 将同时删除文库目录和数据库文件。\n选择 No 仅删除注册记录，保留文件。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            delete_files = answer == QMessageBox.StandardButton.Yes
            try:
                self.viewmodel.delete_library_profile(name, delete_files=delete_files)
            except ValueError as exc:
                QMessageBox.warning(self, "删除文库", str(exc))
                return
            self._show_toast("文库已删除", f"已删除文库“{name}”。", level="success")
            self._open_library_profiles()

    def _lookup_metadata(self) -> None:
        if self._current_literature_id is None:
            return
        literature_id = self._current_literature_id
        self._flush_pending_metadata_changes()
        detail = self.viewmodel.detail_payload(literature_id)
        if not detail:
            return
        manual_identifier = ""
        if not detail.get("doi") and not detail.get("isbn"):
            value, ok = QInputDialog.getText(self, "抓取元数据", "请输入 DOI 或 ISBN（留空则按标题回退检索）")
            if not ok or not value.strip():
                manual_identifier = ""
            else:
                manual_identifier = value.strip()

        def task(controller):
            return controller.lookup_metadata_for_literature(
                literature_id,
                manual_identifier=manual_identifier,
            )

        def handle_result(result) -> None:
            current_detail, payload = result
            if not current_detail or not payload:
                self._show_toast("抓取元数据", "未返回可用元数据。", level="warning")
                return
            preview = MetadataPreviewDialog(payload, self)
            if preview.exec() == 0:
                return
            self._flush_pending_metadata_changes()
            merged = self.viewmodel.apply_metadata_payload(literature_id, payload)
            if merged is None:
                return
            self._refresh_after_library_change(
                preserve_id=literature_id,
                navigation_key=self._current_navigation_key(),
            )
            self._show_toast(
                "元数据已更新",
                "已将缺失字段合并到当前文献。",
                level="success",
            )

        self._run_controller_task(
            label="正在抓取元数据…",
            controller_task=task,
            on_result=handle_result,
            error_title="元数据抓取失败",
        )

    def _open_import_center(self) -> None:
        preserve_id = self._current_literature_id
        dialog = ImportCenterDialog(self.viewmodel.settings, self)
        if dialog.exec() == 0:
            return
        payload = dialog.payload()
        items = payload["items"]
        import_mode = str(payload["import_mode"])

        def task(controller):
            return controller.import_items(items, import_mode=import_mode)

        def handle_result(summary) -> None:
            self._show_toast(
                "导入完成",
                f"成功导入 {summary['imported']} 项，跳过 {summary['skipped']} 项。",
                level="success",
            )

        self._run_controller_task(
            label="正在导入选中项…",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=preserve_id,
            error_title="导入失败",
        )

    def _open_search_center(self) -> None:
        dialog = SearchDialog(self.viewmodel.search_literatures, self)
        if dialog.exec() == 0 or dialog.selected_literature_id is None:
            return
        self.search_bar.line_edit.clear()
        self._refresh_after_library_change(
            preserve_id=dialog.selected_literature_id,
            navigation_key="all",
        )
        self._show_toast("已定位", "已定位到所选文献。", level="info")

    def _export_selected_csl(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_select_literature_toast("导出 CSL")
            return
        initial_dir = self.viewmodel.settings.recent_export_dir or str(Path.cwd())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 CSL JSON",
            str(Path(initial_dir) / "文献导出_csl.json"),
            filter="JSON (*.json)",
        )
        if not path:
            return
        count = self.viewmodel.export_csl_json(literature_ids, path)
        self._show_export_success_toast(count, path)

    def _copy_gbt_reference(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_select_literature_toast("复制 GB/T")
            return
        references = self.viewmodel.build_gbt_references(literature_ids)
        text = "\n".join(reference for reference in references if reference)
        if not text.strip():
            self._show_toast("复制 GB/T", "未生成可用的参考文献。\n请确保文献包含标题、作者等必填字段。", level="warning")
            return
        QApplication.clipboard().setText(text)
        self._show_toast("已复制", f"已复制 {len(references)} 条 GB/T 参考文献。", level="success")

    def _rename_pdfs(self) -> None:
        preserve_id = self._current_literature_id
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_select_literature_toast("PDF 重命名")
            return

        def preview_task(controller):
            return controller.preview_pdf_renames(literature_ids)

        def handle_preview(previews) -> None:
            if not previews:
                self._show_toast("PDF 重命名", "当前选择中没有可重命名的 PDF 附件。", level="warning")
                return
            dialog = RenamePreviewDialog(previews, self)
            if dialog.exec() == 0:
                return

            def rename_task(controller):
                return controller.apply_pdf_renames(previews)

            def handle_renamed(renamed: int) -> None:
                self._show_toast("重命名完成", f"已重命名 {renamed} 个 PDF 文件。", level="success")

            self._run_controller_task(
                label="正在重命名 PDF 文件…",
                controller_task=rename_task,
                on_result=handle_renamed,
                reload_after=True,
                refresh_after=True,
                preserve_id=preserve_id,
                error_title="PDF 重命名失败",
            )

        self._run_controller_task(
            label="正在生成 PDF 重命名预览…",
            controller_task=preview_task,
            on_result=handle_preview,
            error_title="重命名预览失败",
        )

    def _open_dedupe_center(self) -> None:
        def scan_task(controller):
            return controller.find_duplicate_groups()

        def handle_groups(groups) -> None:
            if not groups:
                self._show_toast("重复检测", "未发现重复文献组。", level="info", duration_ms=4200)
                return
            dialog = DuplicateDialog(groups, self)
            if dialog.exec() == 0 or not dialog.result_payload:
                return
            payload = dialog.result_payload

            def merge_task(controller):
                controller.merge_duplicates(payload["primary_id"], payload["merged_ids"], payload["reason"])
                return payload

            def handle_merge(result_payload) -> None:
                self._show_toast(
                    "合并完成",
                    f"已合并 {len(result_payload['merged_ids'])} 条重复记录。",
                    level="success",
                )

            self._run_controller_task(
                label="正在合并重复文献…",
                controller_task=merge_task,
                on_result=handle_merge,
                reload_after=True,
                refresh_after=True,
                preserve_id=payload["primary_id"],
                error_title="重复文献合并失败",
            )

        self._run_controller_task(
            label="正在扫描重复文献…",
            controller_task=scan_task,
            on_result=handle_groups,
            error_title="重复文献扫描失败",
        )

    def _show_statistics_dialog(self) -> None:
        dialog = StatisticsDialog(self.viewmodel.get_statistics(), self)
        if dialog.exec() == 0 or not dialog.export_template_key:
            return
        template_key = dialog.export_template_key
        suffix = self.viewmodel.suggested_export_extension(template_key)
        initial_dir = self.viewmodel.settings.recent_export_dir or str(Path.cwd())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出统计报表",
            str(Path(initial_dir) / f"文库统计{suffix}"),
            filter="Markdown (*.md);;JSON (*.json);;所有文件 (*.*)",
        )
        if not path:
            return
        export_path = self.viewmodel.export_statistics(template_key, path)
        self._show_file_saved_toast("统计报表已导出", export_path)

    def _on_maintenance_dialog_finished(self) -> None:
        self._maintenance_dialog = None

    def _open_maintenance_center(self) -> None:
        if self._maintenance_dialog is None:
            dialog = MaintenanceDialog(self)
            dialog.refreshRequested.connect(self._load_missing_paths)
            dialog.repairRequested.connect(self._repair_missing_paths)
            dialog.rebuildRequested.connect(self._rebuild_search_index)
            dialog.backupRequested.connect(self._create_backup)
            dialog.restoreRequested.connect(self._restore_backup)
            dialog.finished.connect(self._on_maintenance_dialog_finished)
            self._maintenance_dialog = dialog
        self._maintenance_dialog.show()
        self._maintenance_dialog.raise_()
        self._maintenance_dialog.activateWindow()
        self._load_missing_paths()

    def _load_missing_paths(self) -> None:
        if self._maintenance_dialog is None:
            return

        def task(controller):
            return controller.find_missing_paths()

        def handle_result(rows) -> None:
            if self._maintenance_dialog is not None:
                self._maintenance_dialog.set_rows(rows)

        self._run_controller_task(
            label="正在扫描缺失文件…",
            controller_task=task,
            on_result=handle_result,
            error_title="缺失路径扫描失败",
        )

    def _repair_missing_paths(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择修复搜索目录")
        if not folder:
            return

        def task(controller):
            return controller.repair_missing_paths(folder)

        def handle_result(result) -> None:
            if result["unresolved"] > 0:
                msg = f"已修复 {result['fixed']} 项，仍有 {result['unresolved']} 项未解决。\n请查看下方列表了解详情。"
            else:
                msg = f"已修复 {result['fixed']} 项，所有路径已恢复。"
            self._show_toast("修复完成", msg, level="success")
            self._load_missing_paths()

        self._run_controller_task(
            label="正在修复缺失路径…",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=self._current_literature_id,
            error_title="路径修复失败",
        )

    def _rebuild_search_index(self) -> None:
        def task(controller):
            controller.rebuild_search_index()
            return True

        self._run_controller_task(
            label="正在重建全文索引…",
            controller_task=task,
            reload_after=True,
            refresh_after=False,
            success_toast=("索引已重建", "全文检索索引已重建。"),
            error_title="重建索引失败",
        )

    def _create_backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "创建备份",
            str(Path.cwd() / "文献管理工具备份.zip"),
            filter="ZIP (*.zip)",
        )
        if not path:
            return

        def task(controller):
            return controller.create_backup(path)

        def handle_result(backup_path: str) -> None:
            self._show_toast("备份已创建", f"备份已保存到 `{backup_path}`。", level="success")

        self._run_controller_task(
            label="正在创建备份…",
            controller_task=task,
            on_result=handle_result,
            error_title="创建备份失败",
        )

    def _restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择备份文件", filter="ZIP (*.zip)")
        if not path:
            return
        answer = QMessageBox.question(
            self,
            "恢复备份",
            "恢复备份会覆盖当前元数据，是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def task(controller):
            return controller.restore_backup(path)

        def handle_result(_settings) -> None:
            self._show_toast("备份已恢复", "备份恢复完成，工作区已重新加载。", level="success")
            self._load_missing_paths()

        self._run_controller_task(
            label="正在恢复备份…",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=None,
            error_title="恢复备份失败",
        )
    def _create_text_note(self) -> None:
        if self._current_literature_id is None:
            return
        self._loading_notes = True
        self._current_note_id = None
        self._current_note_is_file = False
        self.notes_list.clearSelection()
        self.notes_list.setCurrentItem(None)
        self.note_title_edit.setReadOnly(False)
        self.note_title_edit.setText("")
        self.note_format_combo.setEnabled(True)
        self.note_format_combo.setCurrentIndex(0)
        self.note_body_edit.setReadOnly(False)
        self.note_body_edit.setPlainText("")
        self.note_info_label.setText("正在编辑新的文本笔记，可选择 Markdown 或纯文本格式。")
        self.save_note_button.setEnabled(True)
        self.open_note_file_button.setEnabled(False)
        self._loading_notes = False

    def _link_note_file(self) -> None:
        if self._current_literature_id is None:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "关联笔记文件",
            filter="笔记文件 (*.docx *.md *.markdown *.txt);;所有文件 (*.*)",
        )
        if not selected:
            return
        try:
            note_id = self.viewmodel.save_note(
                literature_id=self._current_literature_id,
                title=Path(selected).stem,
                content="",
                attachment_ids=[],
                note_type="file",
                note_format=detect_note_format(selected),
                external_file_path=selected,
                import_mode=self.viewmodel.settings.default_import_mode,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "关联笔记文件", str(exc))
            return
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(self._current_literature_id, select_note_id=note_id)
        self._show_toast("笔记已关联", "外部笔记文件已关联到当前文献。", level="success")

    def _save_note(self) -> None:
        if self._current_literature_id is None or self._current_note_is_file:
            return
        title = self.note_title_edit.text().strip() or "未命名笔记"
        content = self.note_body_edit.toPlainText()
        note_id = self.viewmodel.save_note(
            literature_id=self._current_literature_id,
            title=title,
            content=content,
            attachment_ids=[],
            note_id=self._current_note_id,
            note_type="text",
            note_format=str(self.note_format_combo.currentData()),
        )
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(self._current_literature_id, select_note_id=note_id)
        self._show_toast("笔记已保存", "笔记内容已更新。", level="success")

    def _delete_selected_note(self) -> None:
        if self._current_note_id is None:
            return
        delete_file = False
        if self._current_note_is_file:
            answer = QMessageBox.question(
                self,
                "删除笔记文件",
                "是否同时删除笔记记录和对应文件？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            delete_file = answer == QMessageBox.StandardButton.Yes
        else:
            answer = QMessageBox.question(self, "删除笔记", "确认删除当前笔记吗？")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.viewmodel.delete_note(self._current_note_id, delete_file=delete_file)
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(self._current_literature_id)
        self._show_toast("笔记已删除", "已删除所选笔记。", level="success")

    def _open_selected_note_file(self) -> None:
        note = self._selected_note_payload()
        if not note or not note.get("resolved_path"):
            return
        try:
            open_path(str(note["resolved_path"]))
        except FileNotFoundError:
            QMessageBox.warning(self, "打开笔记文件", f"文件不存在：\n{note['resolved_path']}")

    def _add_attachments(self) -> None:
        if self._current_literature_id is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "添加附件",
            filter="所有文件 (*.*)",
        )
        if not files:
            return
        dialog = AttachmentDialog(self.viewmodel.settings, self)
        if dialog.exec() == 0:
            return
        try:
            created_ids = self.viewmodel.add_attachments(
                self._current_literature_id,
                files,
                **dialog.value(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "添加附件", str(exc))
            return
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(
            self._current_literature_id,
            select_attachment_id=created_ids[0] if created_ids else None,
        )
        failed_count = len(files) - len(created_ids)
        if failed_count == 0:
            self._show_toast("附件已添加", f"已添加 {len(created_ids)} 个附件。", level="success")
        elif created_ids:
            self._show_toast("附件已添加", f"已添加 {len(created_ids)} 个附件，{failed_count} 个文件添加失败。", level="warning")
        else:
            self._show_toast("添加附件", f"所选 {len(files)} 个文件均添加失败，请检查文件是否存在或可访问。", level="warning")

    def _open_selected_attachment(self) -> None:
        attachment = self._selected_attachment_payload()
        if not attachment:
            self._show_toast("打开附件", "请先选择一个附件。", level="warning")
            return
        preferred_app = ""
        if str(attachment.get("resolved_path", "")).lower().endswith(".pdf"):
            preferred_app = self.viewmodel.settings.pdf_reader_path
        try:
            open_path(str(attachment["resolved_path"]), preferred_app=preferred_app)
        except FileNotFoundError:
            if preferred_app:
                message = f"文件或 PDF 阅读器不存在：\n{attachment['resolved_path']}\n{preferred_app}"
            else:
                message = f"文件不存在：\n{attachment['resolved_path']}"
            QMessageBox.warning(self, "打开附件", message)

    def _reveal_selected_attachment(self) -> None:
        attachment = self._selected_attachment_payload()
        if not attachment:
            self._show_toast("打开所在文件夹", "请先选择一个附件。", level="warning")
            return
        resolved = str(attachment["resolved_path"])
        _logger.debug("Opening parent folder for attachment id=%s path=%s", self._current_attachment_id, resolved)
        try:
            open_parent_folder(resolved)
        except FileNotFoundError:
            QMessageBox.warning(self, "打开所在文件夹", f"文件不存在：\n{resolved}")

    def _delete_selected_attachment(self) -> None:
        if self._current_attachment_id is None:
            return
        answer = QMessageBox.question(
            self,
            "删除附件",
            "是否同时删除附件记录和实际文件？\n"
            "选择“否”则只删除记录。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return
        current_row = self.attachments_list.currentRow()
        total_count = self.attachments_list.count()
        next_id: int | None = None
        if total_count > 1:
            next_row = current_row + 1 if current_row < total_count - 1 else current_row - 1
            next_item = self.attachments_list.item(next_row)
            if next_item is not None:
                next_id = next_item.data(Qt.ItemDataRole.UserRole)
        self.viewmodel.delete_attachment(
            self._current_attachment_id,
            delete_file=answer == QMessageBox.StandardButton.Yes,
        )
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(self._current_literature_id, select_attachment_id=next_id)
        self._show_toast("附件已删除", "已删除所选附件。", level="success")

    def _on_search_requested(self, text: str) -> None:
        self._refresh_table(search_text=text, preserve_id=self._current_literature_id)

    def _on_navigation_changed(self) -> None:
        item = self.navigation_tree.currentItem()
        if item is None:
            return
        navigation = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(navigation, NavigationItem):
            return
        if not navigation.enabled:
            self.statusBar().showMessage(
                navigation.helper_text or "该分区暂未开放。",
                3000,
            )
            return
        self._active_filters = dict(navigation.filters)
        self._refresh_table(preserve_id=self._current_literature_id)
        self.statusBar().showMessage(
            navigation.helper_text or f"已应用筛选：{navigation.label}",
            2500,
        )

    def _on_selection_changed(self) -> None:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            self._show_detail(None)
            return
        literature_id = indexes[0].data(Qt.ItemDataRole.UserRole)
        if isinstance(literature_id, int):
            self._show_detail(literature_id)

    def _on_note_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._loading_notes:
            return
        if current is None:
            self._current_note_id = None
            return
        note_id = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(note_id, int):
            self._populate_note_editor(note_id)

    def _on_attachment_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._loading_attachments:
            return
        if current is not None:
            self._current_attachment_id = current.data(Qt.ItemDataRole.UserRole)
            self.open_attachment_button.setEnabled(True)
            self.reveal_attachment_button.setEnabled(True)
            self.delete_attachment_button.setEnabled(True)
        else:
            self._current_attachment_id = None
            self.open_attachment_button.setEnabled(False)
            self.reveal_attachment_button.setEnabled(False)
            self.delete_attachment_button.setEnabled(False)

    def _on_attachment_double_clicked(self, item: QListWidgetItem) -> None:
        attachment_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(attachment_id, int):
            self._current_attachment_id = attachment_id
            self._open_selected_attachment()

    def _maybe_fetch_more_rows(self, value: int) -> None:
        if value >= self.table.verticalScrollBar().maximum() - 4:
            self._table_model.append_more_if_needed()
    def _show_detail(
        self,
        literature_id: int | None,
        *,
        select_note_id: int | None = None,
        select_attachment_id: int | None = None,
    ) -> None:
        if literature_id != self._current_literature_id:
            self._flush_pending_metadata_changes()
        self._current_literature_id = literature_id
        if literature_id is None:
            self.detail_title.setText("请选择一条文献记录")
            self.detail_subtitle.setText("当前未选中文献")
            self._clear_metadata_fields()
            self._load_notes([])
            self._load_attachments([])
            return

        detail = self.viewmodel.detail_payload(literature_id)
        self._update_detail_header(detail)
        self._populate_metadata(detail)
        self._load_notes(detail.get("notes", []), select_note_id=select_note_id)
        self._load_attachments(detail.get("attachments", []), select_attachment_id=select_attachment_id)

    def _update_detail_header(self, detail: dict) -> None:
        attachment_count = len(detail.get("attachments", []))
        note_count = len(detail.get("notes", []))
        year = detail.get("year", "")
        status = detail.get("reading_status", "")
        self.detail_title.setText(detail.get("title", "未命名文献"))
        self.detail_subtitle.setText(
            " | ".join(
                part
                for part in [str(year) if year else "", status, f"{attachment_count} 个附件", f"{note_count} 条笔记"]
                if part
            )
        )

    def _populate_metadata(self, detail: dict) -> None:
        self._loading_metadata = True
        self.entry_type_combo.setCurrentIndex(max(0, self.entry_type_combo.findData(detail.get("entry_type", "journal_article"))))
        self.title_edit.setText(detail.get("title", "") or "")
        self.subtitle_edit.setText(detail.get("subtitle", "") or "")
        self.short_title_edit.setText(detail.get("short_title", "") or "")
        self.translated_title_edit.setText(detail.get("translated_title", "") or "")
        self.authors_edit.setText(join_csv(detail.get("authors", [])))
        self.translators_edit.setText(detail.get("translators", "") or "")
        self.editors_edit.setText(detail.get("editors", "") or "")
        self.year_edit.setText(str(detail.get("year") or ""))
        self.month_edit.setText(detail.get("month", "") or "")
        self.day_edit.setText(detail.get("day", "") or "")
        self.subject_edit.setText(detail.get("subject", "") or "")
        self.keywords_edit.setText(detail.get("keywords", "") or "")
        self.tags_edit.setText(join_csv(detail.get("tags", [])))
        self.reading_status_combo.setCurrentIndex(
            max(0, self.reading_status_combo.findData(detail.get("reading_status", READING_STATUSES[0]) or READING_STATUSES[0]))
        )
        self.rating_spin.setValue(int(detail.get("rating") or 0))
        self.publication_title_edit.setText(detail.get("publication_title", "") or "")
        self.publisher_edit.setText(detail.get("publisher", "") or "")
        self.publication_place_edit.setText(detail.get("publication_place", "") or "")
        self.school_edit.setText(detail.get("school", "") or "")
        self.institution_edit.setText(detail.get("institution", "") or "")
        self.conference_name_edit.setText(detail.get("conference_name", "") or "")
        self.conference_place_edit.setText(detail.get("conference_place", "") or "")
        self.degree_edit.setText(detail.get("degree", "") or "")
        self.edition_edit.setText(detail.get("edition", "") or "")
        self.standard_number_edit.setText(detail.get("standard_number", "") or "")
        self.patent_number_edit.setText(detail.get("patent_number", "") or "")
        self.report_number_edit.setText(detail.get("report_number", "") or "")
        self.volume_edit.setText(detail.get("volume", "") or "")
        self.issue_edit.setText(detail.get("issue", "") or "")
        self.pages_edit.setText(detail.get("pages", "") or "")
        self.doi_edit.setText(detail.get("doi", "") or "")
        self.isbn_edit.setText(detail.get("isbn", "") or "")
        self.url_edit.setText(detail.get("url", "") or "")
        self.access_date_edit.setText(detail.get("access_date", "") or "")
        self.language_edit.setText(detail.get("language", "") or "")
        self.country_edit.setText(detail.get("country", "") or "")
        self.cite_key_edit.setText(detail.get("cite_key", "") or "")
        self.summary_edit.setPlainText(detail.get("summary", "") or "")
        self.abstract_edit.setPlainText(detail.get("abstract", "") or "")
        self.remarks_edit.setPlainText(detail.get("remarks", "") or "")
        self._apply_entry_type_field_state(clear_hidden=False)
        self._metadata_snapshot = self.viewmodel.normalize_metadata_payload(detail)
        self.metadata_save_label.setText("已保存")
        self._loading_metadata = False

    def _clear_metadata_fields(self) -> None:
        self._loading_metadata = True
        self.entry_type_combo.setCurrentIndex(0)
        self.title_edit.clear()
        self.subtitle_edit.clear()
        self.translated_title_edit.clear()
        self.authors_edit.clear()
        self.translators_edit.clear()
        self.editors_edit.clear()
        self.year_edit.clear()
        self.month_edit.clear()
        self.day_edit.clear()
        self.subject_edit.clear()
        self.keywords_edit.clear()
        self.tags_edit.clear()
        self.reading_status_combo.setCurrentIndex(0)
        self.rating_spin.setValue(0)
        self.publication_title_edit.clear()
        self.publisher_edit.clear()
        self.publication_place_edit.clear()
        self.school_edit.clear()
        self.institution_edit.clear()
        self.conference_name_edit.clear()
        self.conference_place_edit.clear()
        self.degree_edit.clear()
        self.edition_edit.clear()
        self.standard_number_edit.clear()
        self.patent_number_edit.clear()
        self.report_number_edit.clear()
        self.volume_edit.clear()
        self.issue_edit.clear()
        self.pages_edit.clear()
        self.doi_edit.clear()
        self.isbn_edit.clear()
        self.url_edit.clear()
        self.access_date_edit.clear()
        self.language_edit.clear()
        self.country_edit.clear()
        self.cite_key_edit.clear()
        self.summary_edit.clear()
        self.abstract_edit.clear()
        self.remarks_edit.clear()
        self._apply_entry_type_field_state(clear_hidden=False)
        self._metadata_snapshot = None
        self.metadata_save_label.setText("已保存")
        self._loading_metadata = False

    def _load_notes(self, notes: list[dict], *, select_note_id: int | None = None) -> None:
        self._loading_notes = True
        self.notes_list.clear()
        for note in notes:
            descriptor = note.get("title", "")
            if note.get("note_type") == "file":
                descriptor += f" [{note.get('note_format', 'file')}]"
            item = QListWidgetItem(descriptor)
            item.setData(Qt.ItemDataRole.UserRole, int(note["id"]))
            self.notes_list.addItem(item)
        self._loading_notes = False
        if notes:
            target_id = select_note_id or int(notes[0]["id"])
            self._select_note_item(target_id)
        else:
            self._current_note_id = None
            self._current_note_is_file = False
            self.note_title_edit.clear()
            self.note_body_edit.clear()
            self.note_body_edit.setReadOnly(False)
            self.note_title_edit.setReadOnly(False)
            self.note_format_combo.setEnabled(True)
            self.note_info_label.setText("当前文献尚未关联笔记。")
            self.save_note_button.setEnabled(False)
            self.open_note_file_button.setEnabled(False)

    def _select_note_item(self, note_id: int) -> None:
        for row in range(self.notes_list.count()):
            item = self.notes_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == note_id:
                self.notes_list.setCurrentItem(item)
                self._populate_note_editor(note_id)
                return

    def _select_attachment_item(self, attachment_id: int) -> None:
        for row in range(self.attachments_list.count()):
            item = self.attachments_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == attachment_id:
                self.attachments_list.setCurrentItem(item)
                self._current_attachment_id = attachment_id
                self.open_attachment_button.setEnabled(True)
                self.reveal_attachment_button.setEnabled(True)
                self.delete_attachment_button.setEnabled(True)
                return

    def _populate_note_editor(self, note_id: int) -> None:
        note = self.viewmodel.get_note(note_id)
        if not note:
            return
        self._current_note_id = note_id
        self._current_note_is_file = note.get("note_type") == "file"
        self.note_title_edit.setText(note.get("title", "") or "")
        self.note_format_combo.setCurrentIndex(max(0, self.note_format_combo.findData(note.get("note_format", "markdown"))))
        if self._current_note_is_file:
            self.note_title_edit.setReadOnly(True)
            self.note_format_combo.setEnabled(False)
            self.note_body_edit.setReadOnly(True)
            self.note_body_edit.setPlainText(load_note_preview(note.get("resolved_path", "")))
            self.note_info_label.setText(
                f"已关联笔记文件：{note.get('resolved_path', '不可用')}"
            )
            self.save_note_button.setEnabled(False)
            self.open_note_file_button.setEnabled(True)
        else:
            self.note_title_edit.setReadOnly(False)
            self.note_format_combo.setEnabled(True)
            self.note_body_edit.setReadOnly(False)
            self.note_body_edit.setPlainText(note.get("content", "") or "")
            self.note_info_label.setText("可在此直接编辑笔记内容。")
            self.save_note_button.setEnabled(True)
            self.open_note_file_button.setEnabled(False)

    def _selected_note_payload(self) -> dict | None:
        if self._current_note_id is None:
            return None
        return self.viewmodel.get_note(self._current_note_id)

    def _load_attachments(self, attachments: list[dict], *, select_attachment_id: int | None = None) -> None:
        self._loading_attachments = True
        self.attachments_list.clear()
        for attachment in attachments:
            resolved = attachment.get("resolved_path", "")
            filename = Path(resolved).name if resolved else ""
            text = " | ".join(
                part
                for part in [
                    attachment.get("label", ""),
                    ROLE_LABELS.get(attachment.get("role", ""), attachment.get("role", "")),
                    attachment.get("language", ""),
                    filename,
                ]
                if part
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, int(attachment["id"]))
            item.setToolTip(resolved)
            self.attachments_list.addItem(item)
        self._loading_attachments = False
        if attachments:
            target = select_attachment_id or int(attachments[0]["id"])
            self._select_attachment_item(target)
            self.attachments_info_label.setText("打开 PDF 时会优先使用设置中的阅读器，其他文件则使用系统默认程序。")
        else:
            self._current_attachment_id = None
            self.open_attachment_button.setEnabled(False)
            self.reveal_attachment_button.setEnabled(False)
            self.delete_attachment_button.setEnabled(False)
            self.attachments_info_label.setText("当前文献尚未关联附件。点击“添加附件”按钮添加。")

    def _selected_attachment_payload(self) -> dict | None:
        if self._current_attachment_id is None:
            return None
        return self.viewmodel.get_attachment(self._current_attachment_id)

    def _selected_literature_ids(self) -> list[int]:
        ids: list[int] = []
        for index in self.table.selectionModel().selectedRows():
            literature_id = index.data(Qt.ItemDataRole.UserRole)
            if isinstance(literature_id, int) and literature_id not in ids:
                ids.append(literature_id)
        return ids
    def _on_theme_changed(self) -> None:
        requested = str(self.theme_combo.currentData())
        saved = self.viewmodel.set_ui_theme(requested)
        self._apply_theme(saved)

    def _apply_theme(self, requested: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        resolved = apply_theme(app, requested)
        selected_index = self.theme_combo.findData(requested)
        if selected_index >= 0 and selected_index != self.theme_combo.currentIndex():
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(selected_index)
            self.theme_combo.blockSignals(False)
        theme_label = {"system": "跟随系统", "light": "浅色", "dark": "深色"}.get(requested, requested)
        self.statusBar().showMessage(f"主题已切换为：{theme_label}。", 3000)

    def _current_navigation_key(self) -> str | None:
        item = self.navigation_tree.currentItem()
        if item is None:
            return None
        navigation = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(navigation, NavigationItem):
            return navigation.key
        return None

    def _event_has_local_paths(self, event) -> bool:
        return bool(self._event_local_paths(event))

    def _event_local_paths(self, event) -> list[str]:
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return []
        paths: list[str] = []
        for url in mime_data.urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        return paths

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        return label
