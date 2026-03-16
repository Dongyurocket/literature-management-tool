from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import AppSettings, DEFAULT_METADATA_SOURCES
from ...export_service import list_export_templates
from ...utils import available_import_mode_labels

METADATA_SOURCE_LABELS = {
    "crossref": "Crossref?DOI?",
    "datacite": "DataCite?DOI?",
    "openalex": "OpenAlex?DOI / ???",
    "cnki": "????????",
    "ustc_openurl": "?????? OpenURL",
    "tsinghua_openurl": "????? OpenURL",
    "openlibrary": "OpenLibrary?ISBN?",
    "googlebooks": "Google Books?ISBN?",
}


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        *,
        workspace_dir: str = "",
        workspace_locked: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._original = settings
        self._original_workspace_dir = workspace_dir.strip()
        self._workspace_locked = workspace_locked
        self.setWindowTitle("??")
        self.resize(760, 640)

        layout = QVBoxLayout(self)

        basic_form = QFormLayout()
        basic_form.setContentsMargins(12, 12, 12, 12)
        basic_form.setSpacing(12)

        self.library_root_edit = QLineEdit(settings.library_root, self)
        self.library_root_edit.setPlaceholderText("??????????????????")
        library_row = QWidget(self)
        library_layout = QHBoxLayout(library_row)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.addWidget(self.library_root_edit, stretch=1)
        browse_library = QPushButton("??", self)
        browse_library.clicked.connect(self._browse_library_root)
        library_layout.addWidget(browse_library)
        basic_form.addRow("????", library_row)

        self.import_mode_combo = QComboBox(self)
        self._populate_import_modes(settings.default_import_mode, settings.sync_mode_enabled)
        basic_form.addRow("??????", self.import_mode_combo)

        self.pdf_reader_edit = QLineEdit(settings.pdf_reader_path, self)
        reader_row = QWidget(self)
        reader_layout = QHBoxLayout(reader_row)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        reader_layout.addWidget(self.pdf_reader_edit, stretch=1)
        browse_reader = QPushButton("??", self)
        browse_reader.clicked.connect(self._browse_pdf_reader)
        reader_layout.addWidget(browse_reader)
        basic_form.addRow("PDF ???", reader_row)

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("????", "system")
        self.theme_combo.addItem("??", "light")
        self.theme_combo.addItem("??", "dark")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(settings.ui_theme)))
        basic_form.addRow("????", self.theme_combo)

        self.export_template_combo = QComboBox(self)
        for key, label in list_export_templates().items():
            self.export_template_combo.addItem(label, key)
        self.export_template_combo.setCurrentIndex(
            max(0, self.export_template_combo.findData(settings.preferred_export_template))
        )
        basic_form.addRow("??????", self.export_template_combo)

        layout.addLayout(basic_form)

        sync_group = QGroupBox("?????")
        sync_layout = QVBoxLayout(sync_group)
        self.sync_mode_check = QCheckBox("???????????", self)
        self.sync_mode_check.setChecked(settings.sync_mode_enabled)
        self.sync_mode_check.toggled.connect(self._on_sync_mode_toggled)
        sync_layout.addWidget(self.sync_mode_check)

        workspace_form = QFormLayout()
        self.workspace_edit = QLineEdit(self._original_workspace_dir, self)
        self.workspace_edit.setReadOnly(self._workspace_locked)
        workspace_row = QWidget(self)
        workspace_row_layout = QHBoxLayout(workspace_row)
        workspace_row_layout.setContentsMargins(0, 0, 0, 0)
        workspace_row_layout.addWidget(self.workspace_edit, stretch=1)
        browse_workspace = QPushButton("??", self)
        browse_workspace.setEnabled(not self._workspace_locked)
        browse_workspace.clicked.connect(self._browse_workspace_root)
        workspace_row_layout.addWidget(browse_workspace)
        workspace_form.addRow("?????", workspace_row)
        sync_layout.addLayout(workspace_form)

        sync_tip_parts = [
            "?????????????????OneDrive ??????????????????????",
            "????????????????????????????????????",
        ]
        if self._workspace_locked:
            sync_tip_parts.append("?????????? LITERATURE_MANAGER_HOME ???????????")
        sync_tip = QLabel("".join(sync_tip_parts))
        sync_tip.setWordWrap(True)
        sync_layout.addWidget(sync_tip)
        layout.addWidget(sync_group)

        update_group = QGroupBox("??????")
        update_layout = QVBoxLayout(update_group)
        update_form = QFormLayout()

        self.update_repo_edit = QLineEdit(settings.update_repo, self)
        update_form.addRow("GitHub ??", self.update_repo_edit)
        update_layout.addLayout(update_form)

        sources_box = QGroupBox("???????")
        sources_layout = QGridLayout(sources_box)
        self.metadata_source_checks: dict[str, QCheckBox] = {}
        self._syncing_metadata_source_checks = False
        configured_sources: list[str] = []
        seen_sources: set[str] = set()
        for item in settings.metadata_sources or []:
            source = str(item).strip()
            if source not in DEFAULT_METADATA_SOURCES or source in seen_sources:
                continue
            seen_sources.add(source)
            configured_sources.append(source)
        self._metadata_source_order = configured_sources + [
            source for source in DEFAULT_METADATA_SOURCES if source not in seen_sources
        ]
        selected_sources = set(configured_sources or [DEFAULT_METADATA_SOURCES[0]])
        for index, source_key in enumerate(DEFAULT_METADATA_SOURCES):
            checkbox = QCheckBox(METADATA_SOURCE_LABELS[source_key], self)
            checkbox.setChecked(source_key in selected_sources)
            checkbox.toggled.connect(
                lambda checked, key=source_key: self._on_metadata_source_toggled(key, checked)
            )
            self.metadata_source_checks[source_key] = checkbox
            sources_layout.addWidget(checkbox, index // 2, index % 2)
        update_layout.addWidget(sources_box)
        layout.addWidget(update_group)

        tip = QLabel(
            "??/??????????????????????????"
            "??? PDF ??????? PDF ??????"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate_import_modes(self, current_mode: str, sync_mode_enabled: bool) -> None:
        previous = current_mode or str(self.import_mode_combo.currentData() or self._original.default_import_mode)
        self.import_mode_combo.clear()
        mode_labels = available_import_mode_labels(sync_mode_enabled)
        for code, label in mode_labels.items():
            self.import_mode_combo.addItem(label, code)
        target_mode = previous if previous in mode_labels else "copy"
        self.import_mode_combo.setCurrentIndex(max(0, self.import_mode_combo.findData(target_mode)))

    def value(self) -> AppSettings:
        selected_sources = [
            key for key in self._metadata_source_order if self.metadata_source_checks[key].isChecked()
        ] or [DEFAULT_METADATA_SOURCES[0]]
        return AppSettings(
            library_root=self.library_root_edit.text().strip(),
            default_import_mode=str(self.import_mode_combo.currentData()),
            sync_mode_enabled=self.sync_mode_check.isChecked(),
            recent_export_dir=self._original.recent_export_dir,
            pdf_reader_path=self.pdf_reader_edit.text().strip(),
            ui_theme=str(self.theme_combo.currentData()),
            update_repo=self.update_repo_edit.text().strip(),
            metadata_sources=selected_sources,
            preferred_export_template=str(self.export_template_combo.currentData()),
            detail_autosave_enabled=self._original.detail_autosave_enabled,
            detail_autosave_interval_sec=self._original.detail_autosave_interval_sec,
            list_columns=list(self._original.list_columns),
            list_column_widths=dict(self._original.list_column_widths),
        )

    def workspace_dir(self) -> str:
        if self._workspace_locked:
            return self._original_workspace_dir
        return self.workspace_edit.text().strip() or self._original_workspace_dir

    def _on_sync_mode_toggled(self, checked: bool) -> None:
        self._populate_import_modes(str(self.import_mode_combo.currentData()), checked)

    def _on_metadata_source_toggled(self, source_key: str, checked: bool) -> None:
        if self._syncing_metadata_source_checks:
            return
        if checked:
            return

        if any(checkbox.isChecked() for checkbox in self.metadata_source_checks.values()):
            return
        self._syncing_metadata_source_checks = True
        try:
            self.metadata_source_checks[source_key].setChecked(True)
        finally:
            self._syncing_metadata_source_checks = False

    def _browse_library_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "??????")
        if selected:
            self.library_root_edit.setText(selected)

    def _browse_workspace_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "?????????")
        if selected:
            self.workspace_edit.setText(selected)

    def _browse_pdf_reader(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "?? PDF ???",
            filter="????? (*.exe);;???? (*.*)",
        )
        if selected:
            self.pdf_reader_edit.setText(selected)
