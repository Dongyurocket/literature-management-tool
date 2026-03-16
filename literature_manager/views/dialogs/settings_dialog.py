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
from ...utils import IMPORT_MODE_LABELS

METADATA_SOURCE_LABELS = {
    "crossref": "Crossref\uff08DOI\uff09",
    "datacite": "DataCite\uff08DOI\uff09",
    "openalex": "OpenAlex\uff08DOI / \u6807\u9898\uff09",
    "cnki": "\u77e5\u7f51\uff08\u4e2d\u6587\u6587\u732e\uff09",
    "ustc_openurl": "\u4e2d\u79d1\u5927\u56fe\u4e66\u9986 OpenURL",
    "tsinghua_openurl": "\u6e05\u534e\u56fe\u4e66\u9986 OpenURL",
    "openlibrary": "OpenLibrary\uff08ISBN\uff09",
    "googlebooks": "Google Books\uff08ISBN\uff09",
}


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self._original = settings
        self.setWindowTitle("\u8bbe\u7f6e")
        self.resize(760, 560)

        layout = QVBoxLayout(self)

        basic_form = QFormLayout()
        basic_form.setContentsMargins(12, 12, 12, 12)
        basic_form.setSpacing(12)

        self.library_root_edit = QLineEdit(settings.library_root, self)
        library_row = QWidget(self)
        library_layout = QHBoxLayout(library_row)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.addWidget(self.library_root_edit, stretch=1)
        browse_library = QPushButton("\u6d4f\u89c8", self)
        browse_library.clicked.connect(self._browse_library_root)
        library_layout.addWidget(browse_library)
        basic_form.addRow("\u6587\u5e93\u76ee\u5f55", library_row)

        self.import_mode_combo = QComboBox(self)
        for code, label in IMPORT_MODE_LABELS.items():
            self.import_mode_combo.addItem(label, code)
        self.import_mode_combo.setCurrentIndex(
            max(0, self.import_mode_combo.findData(settings.default_import_mode))
        )
        basic_form.addRow("\u9ed8\u8ba4\u5bfc\u5165\u65b9\u5f0f", self.import_mode_combo)

        self.pdf_reader_edit = QLineEdit(settings.pdf_reader_path, self)
        reader_row = QWidget(self)
        reader_layout = QHBoxLayout(reader_row)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        reader_layout.addWidget(self.pdf_reader_edit, stretch=1)
        browse_reader = QPushButton("\u6d4f\u89c8", self)
        browse_reader.clicked.connect(self._browse_pdf_reader)
        reader_layout.addWidget(browse_reader)
        basic_form.addRow("PDF \u9605\u8bfb\u5668", reader_row)

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("\u8ddf\u968f\u7cfb\u7edf", "system")
        self.theme_combo.addItem("\u6d45\u8272", "light")
        self.theme_combo.addItem("\u6df1\u8272", "dark")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(settings.ui_theme)))
        basic_form.addRow("\u754c\u9762\u4e3b\u9898", self.theme_combo)

        self.export_template_combo = QComboBox(self)
        for key, label in list_export_templates().items():
            self.export_template_combo.addItem(label, key)
        self.export_template_combo.setCurrentIndex(
            max(0, self.export_template_combo.findData(settings.preferred_export_template))
        )
        basic_form.addRow("\u9ed8\u8ba4\u5bfc\u51fa\u6a21\u677f", self.export_template_combo)

        layout.addLayout(basic_form)

        update_group = QGroupBox("\u66f4\u65b0\u4e0e\u5143\u6570\u636e")
        update_layout = QVBoxLayout(update_group)
        update_form = QFormLayout()

        self.update_repo_edit = QLineEdit(settings.update_repo, self)
        update_form.addRow("GitHub \u4ed3\u5e93", self.update_repo_edit)
        update_layout.addLayout(update_form)

        sources_box = QGroupBox("\u5143\u6570\u636e\u56de\u9000\u987a\u5e8f")
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
            "\u590d\u5236/\u79fb\u52a8\u5bfc\u5165\u4f1a\u628a\u6587\u4ef6\u5b58\u5165\u6587\u5e93\u76ee\u5f55\uff1b\u4ec5\u5173\u8054\u4f1a\u4fdd\u7559\u539f\u59cb\u4f4d\u7f6e\u3002"
            "\u81ea\u5b9a\u4e49 PDF \u9605\u8bfb\u5668\u4ec5\u5728\u6253\u5f00 PDF \u9644\u4ef6\u65f6\u751f\u6548\u3002"
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

    def value(self) -> AppSettings:
        selected_sources = [
            key for key in self._metadata_source_order if self.metadata_source_checks[key].isChecked()
        ] or [DEFAULT_METADATA_SOURCES[0]]
        return AppSettings(
            library_root=self.library_root_edit.text().strip(),
            default_import_mode=str(self.import_mode_combo.currentData()),
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
        selected = QFileDialog.getExistingDirectory(self, "\u9009\u62e9\u6587\u5e93\u76ee\u5f55")
        if selected:
            self.library_root_edit.setText(selected)

    def _browse_pdf_reader(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "\u9009\u62e9 PDF \u9605\u8bfb\u5668",
            filter="\u53ef\u6267\u884c\u6587\u4ef6 (*.exe);;\u6240\u6709\u6587\u4ef6 (*.*)",
        )
        if selected:
            self.pdf_reader_edit.setText(selected)
