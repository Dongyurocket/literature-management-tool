from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
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

from ..desktop import open_path, reveal_path
from ..models import LiteratureTableModel
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
from .async_worker import AsyncWorker
from .components import SearchBar
from .components import ToastOverlay
from .dialogs import (
    AttachmentDialog,
    DuplicateDialog,
    ImportCenterDialog,
    MaintenanceDialog,
    MetadataPreviewDialog,
    RenamePreviewDialog,
    SearchDialog,
    SettingsDialog,
    StatisticsDialog,
)
from .theme import apply_theme


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
        label = QLabel("Drop PDF, BibTeX, RIS, Markdown, or DOCX files to import")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "font-size: 22px; font-weight: 700; color: #0f6cbd; background: transparent;"
        )
        hint = QLabel("Folders are also supported. Files will use the current default import mode.")
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
        self._loading_notes = False
        self._maintenance_dialog: MaintenanceDialog | None = None
        self._thread_pool = QThreadPool.globalInstance()

        self._metadata_save_timer = QTimer(self)
        self._metadata_save_timer.setInterval(650)
        self._metadata_save_timer.setSingleShot(True)
        self._metadata_save_timer.timeout.connect(self._save_metadata_changes)

        self.setWindowTitle("Literature management tool")
        self.resize(1640, 980)
        self.setAcceptDrops(True)

        self._build_ui()
        self._bind_shortcuts()
        self._apply_theme(self.viewmodel.controller.settings.ui_theme)
        self._load_navigation("all")
        self._refresh_stats()
        self._refresh_table()

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

    def _focus_search(self) -> None:
        self.search_bar.line_edit.setFocus()
        self.search_bar.line_edit.selectAll()

    def _show_toast(self, title: str, message: str, *, level: str = "info", duration_ms: int = 3200) -> None:
        self._toast_overlay.push(title, message, level=level, duration_ms=duration_ms)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.busy_progress.setVisible(busy)
        self.busy_label.setText(message if busy else "Ready")
        if message:
            self.statusBar().showMessage(message, 0 if busy else 3000)

    def _reload_primary_controller(self) -> None:
        settings = self.viewmodel.controller.settings_store.load()
        self.viewmodel.controller.settings = settings
        self.viewmodel.controller.reload_database()
        self._apply_theme(settings.ui_theme)

    def _error_summary(self, error_text: str) -> str:
        parts = [line.strip() for line in error_text.splitlines() if line.strip()]
        return parts[-1] if parts else "Unknown error"

    def _run_async_task(
        self,
        *,
        label: str,
        task,
        on_result=None,
        on_finished=None,
        success_toast: tuple[str, str] | None = None,
        error_title: str = "Operation Failed",
    ) -> None:
        self._set_busy(True, label)
        worker = AsyncWorker(task)

        def handle_result(result) -> None:
            if on_result is not None:
                on_result(result)
            if success_toast is not None:
                self._show_toast(success_toast[0], success_toast[1], level="success")

        def handle_error(error_text: str) -> None:
            self._show_toast(error_title, self._error_summary(error_text), level="error", duration_ms=5200)

        def handle_finished() -> None:
            self._set_busy(False)
            if on_finished is not None:
                on_finished()

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
        error_title: str = "Operation Failed",
    ) -> None:
        def task():
            controller = self.viewmodel.controller.clone(auto_rebuild_index=False)
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
        self.busy_label = QLabel("Ready", self)
        self.busy_progress = QProgressBar(self)
        self.busy_progress.setMaximumWidth(140)
        self.busy_progress.setMaximum(0)
        self.busy_progress.setVisible(False)
        status.addPermanentWidget(self.busy_label)
        status.addPermanentWidget(self.busy_progress)
        status.showMessage("Phase 4 workspace ready.")
        self.setStatusBar(status)

    def _build_header(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("surfaceCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(18)

        hero = QVBoxLayout()
        hero.setSpacing(4)
        title = QLabel("Literature management tool")
        title.setObjectName("heroTitle")
        subtitle = QLabel("Phase 3 workspace with lazy table loading, editable metadata, notes, attachments, and drag-and-drop import.")
        subtitle.setObjectName("heroSubtitle")
        subtitle.setWordWrap(True)
        hero.addWidget(title)
        hero.addWidget(subtitle)
        layout.addLayout(hero, stretch=3)

        self.search_bar = SearchBar(self)
        self.search_bar.setMinimumWidth(420)
        self.search_bar.searchRequested.connect(self._on_search_requested)
        layout.addWidget(self.search_bar, stretch=4)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("System", "system")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        controls.addWidget(self.theme_combo)

        add_button = QPushButton("Add Literature", self)
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._create_literature)
        controls.addWidget(add_button)

        import_button = QPushButton("Quick Import", self)
        import_button.clicked.connect(self._import_files)
        controls.addWidget(import_button)

        export_button = QPushButton("Export Bib", self)
        export_button.clicked.connect(self._export_selected_bib)
        controls.addWidget(export_button)

        search_button = QPushButton("Full-text Search", self)
        search_button.clicked.connect(self._open_search_center)
        controls.addWidget(search_button)

        delete_button = QPushButton("Delete", self)
        delete_button.setObjectName("ghostButton")
        delete_button.clicked.connect(self._delete_current_literature)
        controls.addWidget(delete_button)

        settings_button = QPushButton("Settings", self)
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

        title = QLabel("Navigation")
        title.setObjectName("sectionTitle")
        helper = QLabel("System collections plus dynamic subjects, years, and tags from the database.")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(helper)

        self.navigation_tree = QTreeWidget(card)
        self.navigation_tree.setHeaderHidden(True)
        self.navigation_tree.itemSelectionChanged.connect(self._on_navigation_changed)
        layout.addWidget(self.navigation_tree, stretch=1)

        utility_title = QLabel("Utilities")
        utility_title.setObjectName("sectionTitle")
        layout.addWidget(utility_title)

        utility_grid = QGridLayout()
        utility_grid.setHorizontalSpacing(8)
        utility_grid.setVerticalSpacing(8)
        actions = [
            ("Import Center", self._open_import_center),
            ("Full-text Search", self._open_search_center),
            ("Export CSL", self._export_selected_csl),
            ("Copy GB/T", self._copy_gbt_reference),
            ("Rename PDFs", self._rename_pdfs),
            ("Dedupe", self._open_dedupe_center),
            ("Maintenance", self._open_maintenance_center),
            ("Statistics", self._show_statistics_dialog),
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
        title = QLabel("Literature Data Grid")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.filter_pill = QLabel("All literature")
        self.filter_pill.setObjectName("filterPill")
        header.addWidget(self.filter_pill)
        layout.addLayout(header)

        self.result_hint = QLabel("Search titles, authors, subjects, keywords, abstracts, and use Full-text Search for notes or attachment text.")
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
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 7):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, stretch=1)
        return card
    def _build_right_panel(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Inspector")
        title.setObjectName("sectionTitle")
        helper = QLabel("Edit metadata inline, work with notes, and manage attachments for the selected record.")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(helper)

        self.detail_title = QLabel("Select a literature record")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 700; background: transparent;")
        layout.addWidget(self.detail_title)

        self.detail_subtitle = QLabel("No record selected")
        self.detail_subtitle.setObjectName("mutedLabel")
        layout.addWidget(self.detail_subtitle)

        self.tabs = QTabWidget(card)
        self.tabs.addTab(self._build_metadata_tab(), "Metadata")
        self.tabs.addTab(self._build_notes_tab(), "Notes")
        self.tabs.addTab(self._build_attachments_tab(), "Attachments")
        layout.addWidget(self.tabs, stretch=1)
        return card

    def _build_metadata_tab(self) -> QWidget:
        outer = QWidget(self)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(10)

        action_row = QHBoxLayout()
        self.lookup_metadata_button = QPushButton("Lookup DOI/ISBN", self)
        self.lookup_metadata_button.clicked.connect(self._lookup_metadata)
        self.save_now_button = QPushButton("Save Now", self)
        self.save_now_button.clicked.connect(self._save_metadata_changes)
        self.metadata_save_label = QLabel("Saved")
        self.metadata_save_label.setObjectName("mutedLabel")
        action_row.addWidget(self.lookup_metadata_button)
        action_row.addWidget(self.save_now_button)
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

        grid.addWidget(self._section_label("Basic"), 0, 0)
        grid.addWidget(self._section_label("Publication"), 0, 1)
        grid.addWidget(self._section_label("Extended"), 2, 0)

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
        self.translated_title_edit = QLineEdit(self)
        self.authors_edit = QLineEdit(self)
        self.year_edit = QLineEdit(self)
        self.month_edit = QLineEdit(self)
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
        self.school_edit = QLineEdit(self)
        self.conference_name_edit = QLineEdit(self)
        self.standard_number_edit = QLineEdit(self)
        self.patent_number_edit = QLineEdit(self)
        self.volume_edit = QLineEdit(self)
        self.issue_edit = QLineEdit(self)
        self.pages_edit = QLineEdit(self)
        self.doi_edit = QLineEdit(self)
        self.isbn_edit = QLineEdit(self)
        self.url_edit = QLineEdit(self)
        self.language_edit = QLineEdit(self)
        self.country_edit = QLineEdit(self)

        self.cite_key_edit = QLineEdit(self)
        self.cite_key_edit.setReadOnly(True)
        self.summary_edit = QTextEdit(self)
        self.summary_edit.setFixedHeight(90)
        self.abstract_edit = QTextEdit(self)
        self.abstract_edit.setFixedHeight(120)
        self.remarks_edit = QTextEdit(self)
        self.remarks_edit.setFixedHeight(120)

        basic_form.addRow("Type", self.entry_type_combo)
        basic_form.addRow("Title", self.title_edit)
        basic_form.addRow("Translated Title", self.translated_title_edit)
        basic_form.addRow("Authors", self.authors_edit)
        basic_form.addRow("Year", self.year_edit)
        basic_form.addRow("Month", self.month_edit)
        basic_form.addRow("Subject", self.subject_edit)
        basic_form.addRow("Keywords", self.keywords_edit)
        basic_form.addRow("Tags", self.tags_edit)
        basic_form.addRow("Reading Status", self.reading_status_combo)
        basic_form.addRow("Rating", self.rating_spin)

        publication_form.addRow("Publication", self.publication_title_edit)
        publication_form.addRow("Publisher", self.publisher_edit)
        publication_form.addRow("School", self.school_edit)
        publication_form.addRow("Conference", self.conference_name_edit)
        publication_form.addRow("Standard No.", self.standard_number_edit)
        publication_form.addRow("Patent No.", self.patent_number_edit)
        publication_form.addRow("Volume", self.volume_edit)
        publication_form.addRow("Issue", self.issue_edit)
        publication_form.addRow("Pages", self.pages_edit)
        publication_form.addRow("DOI", self.doi_edit)
        publication_form.addRow("ISBN", self.isbn_edit)
        publication_form.addRow("URL", self.url_edit)
        publication_form.addRow("Language", self.language_edit)
        publication_form.addRow("Country", self.country_edit)

        extra_form.addRow("Citation Key", self.cite_key_edit)
        extra_form.addRow("Summary", self.summary_edit)
        extra_form.addRow("Abstract", self.abstract_edit)
        extra_form.addRow("Remarks", self.remarks_edit)

        self._connect_metadata_autosave()
        scroll.setWidget(container)
        outer_layout.addWidget(scroll, stretch=1)
        return outer

    def _build_notes_tab(self) -> QWidget:
        card = QWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        button_row = QHBoxLayout()
        new_note_button = QPushButton("New Text Note", self)
        new_note_button.clicked.connect(self._create_text_note)
        link_note_button = QPushButton("Link Note File", self)
        link_note_button.clicked.connect(self._link_note_file)
        self.save_note_button = QPushButton("Save Note", self)
        self.save_note_button.clicked.connect(self._save_note)
        self.delete_note_button = QPushButton("Delete Note", self)
        self.delete_note_button.clicked.connect(self._delete_selected_note)
        self.open_note_file_button = QPushButton("Open File", self)
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
        self.note_title_edit.setPlaceholderText("Note title")
        self.note_format_combo = QComboBox(self)
        self.note_format_combo.addItem("Markdown", "markdown")
        self.note_format_combo.addItem("Text", "text")
        top_row.addWidget(self.note_title_edit, stretch=1)
        top_row.addWidget(self.note_format_combo)
        editor_layout.addLayout(top_row)

        self.note_info_label = QLabel("Create a text note or select an existing note.")
        self.note_info_label.setObjectName("mutedLabel")
        self.note_info_label.setWordWrap(True)
        editor_layout.addWidget(self.note_info_label)

        self.note_body_edit = QTextEdit(self)
        self.note_body_edit.setPlaceholderText("Markdown and plain text note content")
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
        add_button = QPushButton("Add Files", self)
        add_button.clicked.connect(self._add_attachments)
        self.open_attachment_button = QPushButton("Open", self)
        self.open_attachment_button.clicked.connect(self._open_selected_attachment)
        self.reveal_attachment_button = QPushButton("Reveal", self)
        self.reveal_attachment_button.clicked.connect(self._reveal_selected_attachment)
        self.delete_attachment_button = QPushButton("Delete", self)
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
        layout.addWidget(self.attachments_list, stretch=1)

        self.attachments_info_label = QLabel("Select an attachment to open it with the configured PDF reader or the system default app.")
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
        rows = self.viewmodel.list_rows(
            search=search_text if search_text is not None else self.search_bar.text(),
            filters=self._active_filters,
        )
        self._table_model.set_rows(rows)
        self.result_hint.setText(
            f"{self._table_model.total_count()} item(s) matched. The grid loads rows in batches as you scroll."
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

    def _connect_metadata_autosave(self) -> None:
        widgets = [
            self.entry_type_combo,
            self.title_edit,
            self.translated_title_edit,
            self.authors_edit,
            self.year_edit,
            self.month_edit,
            self.subject_edit,
            self.keywords_edit,
            self.tags_edit,
            self.reading_status_combo,
            self.publication_title_edit,
            self.publisher_edit,
            self.school_edit,
            self.conference_name_edit,
            self.standard_number_edit,
            self.patent_number_edit,
            self.volume_edit,
            self.issue_edit,
            self.pages_edit,
            self.doi_edit,
            self.isbn_edit,
            self.url_edit,
            self.language_edit,
            self.country_edit,
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
        self.metadata_save_label.setText("Saving soon...")
        self._metadata_save_timer.start()

    def _save_metadata_changes(self) -> None:
        if self._loading_metadata or self._current_literature_id is None:
            return
        detail = self.viewmodel.detail_payload(self._current_literature_id)
        if not detail:
            return
        payload = dict(detail)
        payload.update(
            {
                "id": self._current_literature_id,
                "entry_type": str(self.entry_type_combo.currentData()),
                "title": self.title_edit.text().strip() or "Untitled Record",
                "translated_title": self.translated_title_edit.text().strip(),
                "authors": split_csv(self.authors_edit.text().replace("\n", ",")),
                "year": int(self.year_edit.text()) if self.year_edit.text().strip().isdigit() else None,
                "month": self.month_edit.text().strip(),
                "subject": self.subject_edit.text().strip(),
                "keywords": self.keywords_edit.text().strip(),
                "tags": split_csv(self.tags_edit.text().replace("\n", ",")),
                "reading_status": str(self.reading_status_combo.currentData()),
                "rating": self.rating_spin.value() or None,
                "publication_title": self.publication_title_edit.text().strip(),
                "publisher": self.publisher_edit.text().strip(),
                "school": self.school_edit.text().strip(),
                "conference_name": self.conference_name_edit.text().strip(),
                "standard_number": self.standard_number_edit.text().strip(),
                "patent_number": self.patent_number_edit.text().strip(),
                "volume": self.volume_edit.text().strip(),
                "issue": self.issue_edit.text().strip(),
                "pages": self.pages_edit.text().strip(),
                "doi": self.doi_edit.text().strip(),
                "isbn": self.isbn_edit.text().strip(),
                "url": self.url_edit.text().strip(),
                "language": self.language_edit.text().strip(),
                "country": self.country_edit.text().strip(),
                "summary": self.summary_edit.toPlainText().strip(),
                "abstract": self.abstract_edit.toPlainText().strip(),
                "remarks": self.remarks_edit.toPlainText().strip(),
                "cite_key": self.cite_key_edit.text().strip(),
            }
        )
        self.viewmodel.controller.save_literature(payload)
        refreshed = self.viewmodel.detail_payload(self._current_literature_id)
        self._update_detail_header(refreshed)
        self.cite_key_edit.setText(refreshed.get("cite_key", "") or "")
        self.metadata_save_label.setText("Saved")
        self.statusBar().showMessage("Metadata saved.", 2500)

    def _create_literature(self) -> None:
        literature_id = self.viewmodel.controller.save_literature(
            {
                "entry_type": "journal_article",
                "title": "Untitled Record",
                "authors": [],
                "tags": [],
                "reading_status": READING_STATUSES[0],
            }
        )
        self.search_bar.line_edit.clear()
        self._refresh_after_library_change(preserve_id=literature_id, navigation_key="all")
        self._show_toast("Created", "A new literature record is ready for editing.", level="success")

    def _delete_current_literature(self) -> None:
        if self._current_literature_id is None:
            return
        detail = self.viewmodel.detail_payload(self._current_literature_id)
        answer = QMessageBox.question(
            self,
            "Delete literature",
            f"Delete '{detail.get('title', 'Untitled')}' and all linked notes/attachments records?\n"
            "Underlying files are not removed automatically.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted_id = self._current_literature_id
        self.viewmodel.controller.delete_literature(deleted_id)
        self._refresh_after_library_change(navigation_key=self._current_navigation_key())
        self._show_toast("Deleted", f"Literature #{deleted_id} was removed.", level="success")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.viewmodel.controller.settings, self)
        if dialog.exec() == 0:
            return
        settings = dialog.value()
        self.viewmodel.controller.save_settings(settings)
        self._apply_theme(settings.ui_theme)
        self._show_toast("Settings Saved", "Desktop preferences were updated.", level="success")

    def _import_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Import literature files",
            filter="Supported files (*.pdf *.bib *.ris *.docx *.md *.markdown *.txt);;All files (*.*)",
        )
        if selected:
            self._import_paths(selected)

    def _import_paths(self, paths: list[str]) -> None:
        def task(controller):
            return controller.import_paths(paths)

        def handle_result(result) -> None:
            items, summary = result
            if not items:
                self._show_toast(
                    "Nothing Imported",
                    "No supported files were found in the selected or dropped paths.",
                    level="warning",
                )
                return
            self._show_toast(
                "Import Completed",
                f"Imported {summary['imported']} item(s), skipped {summary['skipped']}.",
                level="success",
            )

        self._run_controller_task(
            label="Importing files in the background...",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=self._current_literature_id,
            error_title="Import Failed",
        )

    def _export_selected_bib(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            QMessageBox.information(self, "Export Bib", "Select at least one literature record to export.")
            return
        initial_dir = self.viewmodel.controller.settings.recent_export_dir or str(Path.cwd())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export BibTeX",
            str(Path(initial_dir) / "literature_export.bib"),
            filter="BibTeX (*.bib)",
        )
        if not path:
            return
        count = self.viewmodel.controller.export_bib(literature_ids, path)
        self._show_toast("BibTeX Exported", f"Exported {count} record(s) to `{path}`.", level="success")

    def _lookup_metadata(self) -> None:
        if self._current_literature_id is None:
            return
        detail = self.viewmodel.controller.get_literature(self._current_literature_id)
        if not detail:
            return
        manual_identifier = ""
        if not detail.get("doi") and not detail.get("isbn"):
            value, ok = QInputDialog.getText(self, "Lookup DOI/ISBN", "Enter DOI or ISBN")
            if not ok or not value.strip():
                return
            manual_identifier = value.strip()

        def task(controller):
            return controller.lookup_metadata_for_literature(
                self._current_literature_id,
                manual_identifier=manual_identifier,
            )

        def handle_result(result) -> None:
            current_detail, payload = result
            if not current_detail or not payload:
                self._show_toast("Lookup DOI/ISBN", "No metadata was returned.", level="warning")
                return
            preview = MetadataPreviewDialog(payload, self)
            if preview.exec() == 0:
                return
            merged = self.viewmodel.controller.apply_metadata_payload(self._current_literature_id, payload)
            if merged is None:
                return
            self._refresh_after_library_change(
                preserve_id=self._current_literature_id,
                navigation_key=self._current_navigation_key(),
            )
            self._show_toast(
                "Metadata Updated",
                "Missing fields were merged from DOI/ISBN lookup.",
                level="success",
            )

        self._run_controller_task(
            label="Fetching metadata from DOI/ISBN...",
            controller_task=task,
            on_result=handle_result,
            error_title="Metadata Lookup Failed",
        )

    def _open_import_center(self) -> None:
        dialog = ImportCenterDialog(self.viewmodel.controller.settings, self)
        if dialog.exec() == 0:
            return
        payload = dialog.payload()
        items = payload["items"]
        import_mode = str(payload["import_mode"])

        def task(controller):
            return controller.import_items(items, import_mode=import_mode)

        def handle_result(summary) -> None:
            self._show_toast(
                "Import Completed",
                f"Imported {summary['imported']} item(s), skipped {summary['skipped']}.",
                level="success",
            )

        self._run_controller_task(
            label="Importing selected items...",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=self._current_literature_id,
            error_title="Import Failed",
        )

    def _open_search_center(self) -> None:
        dialog = SearchDialog(self.viewmodel.controller, self)
        if dialog.exec() == 0 or dialog.selected_literature_id is None:
            return
        self.search_bar.line_edit.clear()
        self._refresh_after_library_change(
            preserve_id=dialog.selected_literature_id,
            navigation_key="all",
        )
        self._show_toast("Search Located", "Focused the selected literature record.", level="info")

    def _export_selected_csl(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_toast("Export CSL", "Select at least one literature record.", level="warning")
            return
        initial_dir = self.viewmodel.controller.settings.recent_export_dir or str(Path.cwd())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSL JSON",
            str(Path(initial_dir) / "literature_export_csl.json"),
            filter="JSON (*.json)",
        )
        if not path:
            return
        count = self.viewmodel.controller.export_csl_json(literature_ids, path)
        self._show_toast("CSL Exported", f"Exported {count} record(s) to `{path}`.", level="success")

    def _copy_gbt_reference(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_toast("Copy GB/T", "Select at least one literature record.", level="warning")
            return
        references = self.viewmodel.controller.build_gbt_references(literature_ids)
        text = "\n".join(reference for reference in references if reference)
        if not text.strip():
            self._show_toast("Copy GB/T", "No references were generated.", level="warning")
            return
        QApplication.clipboard().setText(text)
        self._show_toast("Copied", f"Copied {len(references)} GB/T reference(s) to the clipboard.", level="success")

    def _rename_pdfs(self) -> None:
        literature_ids = self._selected_literature_ids() or ([self._current_literature_id] if self._current_literature_id else [])
        if not literature_ids:
            self._show_toast("Rename PDFs", "Select at least one literature record.", level="warning")
            return

        def preview_task(controller):
            return controller.preview_pdf_renames(literature_ids)

        def handle_preview(previews) -> None:
            if not previews:
                self._show_toast("Rename PDFs", "No PDF attachments are available for renaming.", level="warning")
                return
            dialog = RenamePreviewDialog(previews, self)
            if dialog.exec() == 0:
                return

            def rename_task(controller):
                return controller.apply_pdf_renames(previews)

            def handle_renamed(renamed: int) -> None:
                self._show_toast("Rename Completed", f"Renamed {renamed} PDF file(s).", level="success")

            self._run_controller_task(
                label="Renaming PDF files...",
                controller_task=rename_task,
                on_result=handle_renamed,
                reload_after=True,
                refresh_after=True,
                preserve_id=self._current_literature_id,
                error_title="Rename Failed",
            )

        self._run_controller_task(
            label="Preparing PDF rename preview...",
            controller_task=preview_task,
            on_result=handle_preview,
            error_title="Rename Preview Failed",
        )

    def _open_dedupe_center(self) -> None:
        def scan_task(controller):
            return controller.find_duplicate_groups()

        def handle_groups(groups) -> None:
            if not groups:
                self._show_toast("Dedupe", "No duplicate literature groups were found.", level="info")
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
                    "Merge Completed",
                    f"Merged {len(result_payload['merged_ids'])} duplicate record(s).",
                    level="success",
                )

            self._run_controller_task(
                label="Merging duplicate literature records...",
                controller_task=merge_task,
                on_result=handle_merge,
                reload_after=True,
                refresh_after=True,
                preserve_id=payload["primary_id"],
                error_title="Duplicate Merge Failed",
            )

        self._run_controller_task(
            label="Scanning for duplicate literature records...",
            controller_task=scan_task,
            on_result=handle_groups,
            error_title="Duplicate Scan Failed",
        )

    def _show_statistics_dialog(self) -> None:
        dialog = StatisticsDialog(self.viewmodel.controller.get_statistics(), self)
        dialog.exec()

    def _open_maintenance_center(self) -> None:
        if self._maintenance_dialog is None:
            dialog = MaintenanceDialog(self)
            dialog.refreshRequested.connect(self._load_missing_paths)
            dialog.repairRequested.connect(self._repair_missing_paths)
            dialog.rebuildRequested.connect(self._rebuild_search_index)
            dialog.backupRequested.connect(self._create_backup)
            dialog.restoreRequested.connect(self._restore_backup)
            dialog.finished.connect(lambda _code=0: setattr(self, "_maintenance_dialog", None))
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
            label="Scanning for missing files...",
            controller_task=task,
            on_result=handle_result,
            error_title="Missing Path Scan Failed",
        )

    def _repair_missing_paths(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select repair search folder")
        if not folder:
            return

        def task(controller):
            return controller.repair_missing_paths(folder)

        def handle_result(result) -> None:
            self._show_toast(
                "Repair Completed",
                f"Fixed {result['fixed']} item(s), {result['unresolved']} still unresolved.",
                level="success",
            )
            self._load_missing_paths()

        self._run_controller_task(
            label="Repairing missing paths...",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=self._current_literature_id,
            error_title="Repair Failed",
        )

    def _rebuild_search_index(self) -> None:
        def task(controller):
            controller.rebuild_search_index()
            return True

        self._run_controller_task(
            label="Rebuilding full-text search index...",
            controller_task=task,
            reload_after=True,
            refresh_after=False,
            success_toast=("Index Rebuilt", "The full-text search index was rebuilt."),
            error_title="Rebuild Index Failed",
        )

    def _create_backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Create Backup",
            str(Path.cwd() / "literature_manager_backup.zip"),
            filter="ZIP (*.zip)",
        )
        if not path:
            return

        def task(controller):
            return controller.create_backup(path)

        def handle_result(backup_path: str) -> None:
            self._show_toast("Backup Created", f"Backup saved to `{backup_path}`.", level="success")

        self._run_controller_task(
            label="Creating backup archive...",
            controller_task=task,
            on_result=handle_result,
            error_title="Backup Failed",
        )

    def _restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select backup archive", filter="ZIP (*.zip)")
        if not path:
            return
        answer = QMessageBox.question(
            self,
            "Restore Backup",
            "Restoring a backup overwrites the current metadata. Continue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def task(controller):
            return controller.restore_backup(path)

        def handle_result(_settings) -> None:
            self._show_toast("Backup Restored", "The backup was restored and the workspace reloaded.", level="success")
            self._load_missing_paths()

        self._run_controller_task(
            label="Restoring backup archive...",
            controller_task=task,
            on_result=handle_result,
            reload_after=True,
            refresh_after=True,
            preserve_id=None,
            error_title="Restore Failed",
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
        self.note_info_label.setText("Editing a new text note. Choose Markdown or plain text and save when ready.")
        self.save_note_button.setEnabled(True)
        self.open_note_file_button.setEnabled(False)
        self._loading_notes = False

    def _link_note_file(self) -> None:
        if self._current_literature_id is None:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Link note file",
            filter="Note files (*.docx *.md *.markdown *.txt);;All files (*.*)",
        )
        if not selected:
            return
        try:
            note_id = self.viewmodel.controller.save_note(
                literature_id=self._current_literature_id,
                title=Path(selected).stem,
                content="",
                attachment_ids=[],
                note_type="file",
                note_format=detect_note_format(selected),
                external_file_path=selected,
                import_mode=self.viewmodel.controller.settings.default_import_mode,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Link note file", str(exc))
            return
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(self._current_literature_id, select_note_id=note_id)
        self._show_toast("Note File Linked", "The external note file is now attached to this literature record.", level="success")

    def _save_note(self) -> None:
        if self._current_literature_id is None or self._current_note_is_file:
            return
        title = self.note_title_edit.text().strip() or "Untitled Note"
        content = self.note_body_edit.toPlainText()
        note_id = self.viewmodel.controller.save_note(
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
        self._show_toast("Note Saved", "The note content was updated.", level="success")

    def _delete_selected_note(self) -> None:
        if self._current_note_id is None:
            return
        delete_file = False
        if self._current_note_is_file:
            answer = QMessageBox.question(
                self,
                "Delete note file",
                "Delete the linked note record as well as the underlying file?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            delete_file = answer == QMessageBox.StandardButton.Yes
        else:
            answer = QMessageBox.question(self, "Delete note", "Delete the selected note?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.viewmodel.controller.delete_note(self._current_note_id, delete_file=delete_file)
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(self._current_literature_id)
        self._show_toast("Note Deleted", "The selected note was removed.", level="success")

    def _open_selected_note_file(self) -> None:
        note = self._selected_note_payload()
        if not note or not note.get("resolved_path"):
            return
        try:
            open_path(str(note["resolved_path"]))
        except FileNotFoundError:
            QMessageBox.warning(self, "Open note file", f"File not found:\n{note['resolved_path']}")

    def _add_attachments(self) -> None:
        if self._current_literature_id is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add attachments",
            filter="All files (*.*)",
        )
        if not files:
            return
        dialog = AttachmentDialog(self.viewmodel.controller.settings, self)
        if dialog.exec() == 0:
            return
        try:
            created_ids = self.viewmodel.controller.add_attachments(
                self._current_literature_id,
                files,
                **dialog.value(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Add attachments", str(exc))
            return
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(
            self._current_literature_id,
            select_attachment_id=created_ids[0] if created_ids else None,
        )
        self._show_toast("Attachments Added", f"Added {len(created_ids)} attachment(s).", level="success")

    def _open_selected_attachment(self) -> None:
        attachment = self._selected_attachment_payload()
        if not attachment:
            return
        preferred_app = ""
        if str(attachment.get("resolved_path", "")).lower().endswith(".pdf"):
            preferred_app = self.viewmodel.controller.settings.pdf_reader_path
        try:
            open_path(str(attachment["resolved_path"]), preferred_app=preferred_app)
        except FileNotFoundError:
            if preferred_app:
                message = f"File or PDF reader was not found:\n{attachment['resolved_path']}\n{preferred_app}"
            else:
                message = f"File not found:\n{attachment['resolved_path']}"
            QMessageBox.warning(self, "Open attachment", message)

    def _reveal_selected_attachment(self) -> None:
        attachment = self._selected_attachment_payload()
        if not attachment:
            return
        try:
            reveal_path(str(attachment["resolved_path"]))
        except FileNotFoundError:
            QMessageBox.warning(self, "Reveal attachment", f"File not found:\n{attachment['resolved_path']}")

    def _delete_selected_attachment(self) -> None:
        if self._current_attachment_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete attachment",
            "Delete the selected attachment and the underlying file?\n"
            "Choose No to remove only the attachment record.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return
        self.viewmodel.controller.delete_attachment(
            self._current_attachment_id,
            delete_file=answer == QMessageBox.StandardButton.Yes,
        )
        self._refresh_stats()
        self._refresh_table(preserve_id=self._current_literature_id)
        self._show_detail(self._current_literature_id)
        self._show_toast("Attachment Deleted", "The selected attachment was removed.", level="success")

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
                navigation.helper_text or "This section is reserved for a later phase.",
                3000,
            )
            return
        self._active_filters = dict(navigation.filters)
        self._refresh_table(preserve_id=self._current_literature_id)
        self.statusBar().showMessage(
            navigation.helper_text or f"Applied filter: {navigation.label}",
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
        self._current_attachment_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None

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
        self._current_literature_id = literature_id
        if literature_id is None:
            self.detail_title.setText("Select a literature record")
            self.detail_subtitle.setText("No record selected")
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
        self.detail_title.setText(detail.get("title", "Untitled"))
        self.detail_subtitle.setText(
            " | ".join(
                part
                for part in [str(year) if year else "", status, f"{attachment_count} attachment(s)", f"{note_count} note(s)"]
                if part
            )
        )

    def _populate_metadata(self, detail: dict) -> None:
        self._loading_metadata = True
        self.entry_type_combo.setCurrentIndex(max(0, self.entry_type_combo.findData(detail.get("entry_type", "journal_article"))))
        self.title_edit.setText(detail.get("title", "") or "")
        self.translated_title_edit.setText(detail.get("translated_title", "") or "")
        self.authors_edit.setText(join_csv(detail.get("authors", [])))
        self.year_edit.setText(str(detail.get("year") or ""))
        self.month_edit.setText(detail.get("month", "") or "")
        self.subject_edit.setText(detail.get("subject", "") or "")
        self.keywords_edit.setText(detail.get("keywords", "") or "")
        self.tags_edit.setText(join_csv(detail.get("tags", [])))
        self.reading_status_combo.setCurrentIndex(
            max(0, self.reading_status_combo.findData(detail.get("reading_status", READING_STATUSES[0]) or READING_STATUSES[0]))
        )
        self.rating_spin.setValue(int(detail.get("rating") or 0))
        self.publication_title_edit.setText(detail.get("publication_title", "") or "")
        self.publisher_edit.setText(detail.get("publisher", "") or "")
        self.school_edit.setText(detail.get("school", "") or "")
        self.conference_name_edit.setText(detail.get("conference_name", "") or "")
        self.standard_number_edit.setText(detail.get("standard_number", "") or "")
        self.patent_number_edit.setText(detail.get("patent_number", "") or "")
        self.volume_edit.setText(detail.get("volume", "") or "")
        self.issue_edit.setText(detail.get("issue", "") or "")
        self.pages_edit.setText(detail.get("pages", "") or "")
        self.doi_edit.setText(detail.get("doi", "") or "")
        self.isbn_edit.setText(detail.get("isbn", "") or "")
        self.url_edit.setText(detail.get("url", "") or "")
        self.language_edit.setText(detail.get("language", "") or "")
        self.country_edit.setText(detail.get("country", "") or "")
        self.cite_key_edit.setText(detail.get("cite_key", "") or "")
        self.summary_edit.setPlainText(detail.get("summary", "") or "")
        self.abstract_edit.setPlainText(detail.get("abstract", "") or "")
        self.remarks_edit.setPlainText(detail.get("remarks", "") or "")
        self.metadata_save_label.setText("Saved")
        self._loading_metadata = False

    def _clear_metadata_fields(self) -> None:
        self._loading_metadata = True
        self.title_edit.clear()
        self.translated_title_edit.clear()
        self.authors_edit.clear()
        self.year_edit.clear()
        self.month_edit.clear()
        self.subject_edit.clear()
        self.keywords_edit.clear()
        self.tags_edit.clear()
        self.rating_spin.setValue(0)
        self.publication_title_edit.clear()
        self.publisher_edit.clear()
        self.school_edit.clear()
        self.conference_name_edit.clear()
        self.standard_number_edit.clear()
        self.patent_number_edit.clear()
        self.volume_edit.clear()
        self.issue_edit.clear()
        self.pages_edit.clear()
        self.doi_edit.clear()
        self.isbn_edit.clear()
        self.url_edit.clear()
        self.language_edit.clear()
        self.country_edit.clear()
        self.cite_key_edit.clear()
        self.summary_edit.clear()
        self.abstract_edit.clear()
        self.remarks_edit.clear()
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
            self.note_info_label.setText("No notes linked to this literature record.")
            self.save_note_button.setEnabled(False)
            self.open_note_file_button.setEnabled(False)

    def _select_note_item(self, note_id: int) -> None:
        for row in range(self.notes_list.count()):
            item = self.notes_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == note_id:
                self.notes_list.setCurrentItem(item)
                self._populate_note_editor(note_id)
                return

    def _populate_note_editor(self, note_id: int) -> None:
        note = self.viewmodel.controller.get_note(note_id)
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
                f"Linked file note: {note.get('resolved_path', 'Unavailable')}"
            )
            self.save_note_button.setEnabled(False)
            self.open_note_file_button.setEnabled(True)
        else:
            self.note_title_edit.setReadOnly(False)
            self.note_format_combo.setEnabled(True)
            self.note_body_edit.setReadOnly(False)
            self.note_body_edit.setPlainText(note.get("content", "") or "")
            self.note_info_label.setText("Inline note content is editable here.")
            self.save_note_button.setEnabled(True)
            self.open_note_file_button.setEnabled(False)

    def _selected_note_payload(self) -> dict | None:
        if self._current_note_id is None:
            return None
        return self.viewmodel.controller.get_note(self._current_note_id)

    def _load_attachments(self, attachments: list[dict], *, select_attachment_id: int | None = None) -> None:
        self.attachments_list.clear()
        self._current_attachment_id = None
        for attachment in attachments:
            text = " | ".join(
                part
                for part in [
                    attachment.get("label", ""),
                    ROLE_LABELS.get(attachment.get("role", ""), attachment.get("role", "")),
                    attachment.get("language", ""),
                    attachment.get("resolved_path", ""),
                ]
                if part
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, int(attachment["id"]))
            self.attachments_list.addItem(item)
        if attachments:
            target = select_attachment_id or int(attachments[0]["id"])
            for row in range(self.attachments_list.count()):
                item = self.attachments_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == target:
                    self.attachments_list.setCurrentItem(item)
                    self._current_attachment_id = target
                    break
        self.open_attachment_button.setEnabled(bool(attachments))
        self.reveal_attachment_button.setEnabled(bool(attachments))
        self.delete_attachment_button.setEnabled(bool(attachments))

    def _selected_attachment_payload(self) -> dict | None:
        if self._current_attachment_id is None:
            return None
        return self.viewmodel.controller.get_attachment(self._current_attachment_id)

    def _selected_literature_ids(self) -> list[int]:
        ids: list[int] = []
        for index in self.table.selectionModel().selectedRows():
            literature_id = index.data(Qt.ItemDataRole.UserRole)
            if isinstance(literature_id, int) and literature_id not in ids:
                ids.append(literature_id)
        return ids
    def _on_theme_changed(self) -> None:
        requested = str(self.theme_combo.currentData())
        saved = self.viewmodel.controller.set_ui_theme(requested)
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
        self.statusBar().showMessage(f"Theme set to {requested} ({resolved} palette applied).", 3000)

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
