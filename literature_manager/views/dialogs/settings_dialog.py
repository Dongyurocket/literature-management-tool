from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import AppSettings
from ...utils import IMPORT_MODE_LABELS


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self._original = settings
        self.setWindowTitle("Settings")
        self.resize(620, 320)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(12)

        self.library_root_edit = QLineEdit(settings.library_root, self)
        library_row = QWidget(self)
        library_layout = QHBoxLayout(library_row)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.addWidget(self.library_root_edit, stretch=1)
        browse_library = QPushButton("Browse", self)
        browse_library.clicked.connect(self._browse_library_root)
        library_layout.addWidget(browse_library)
        form.addRow("Library Root", library_row)

        self.import_mode_combo = QComboBox(self)
        for code, label in IMPORT_MODE_LABELS.items():
            self.import_mode_combo.addItem(label, code)
        self.import_mode_combo.setCurrentIndex(
            max(0, self.import_mode_combo.findData(settings.default_import_mode))
        )
        form.addRow("Default Import", self.import_mode_combo)

        self.pdf_reader_edit = QLineEdit(settings.pdf_reader_path, self)
        reader_row = QWidget(self)
        reader_layout = QHBoxLayout(reader_row)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        reader_layout.addWidget(self.pdf_reader_edit, stretch=1)
        browse_reader = QPushButton("Browse", self)
        browse_reader.clicked.connect(self._browse_pdf_reader)
        reader_layout.addWidget(browse_reader)
        form.addRow("PDF Reader", reader_row)

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("System", "system")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(settings.ui_theme)))
        form.addRow("Theme", self.theme_combo)

        tip = QLabel(
            "Copy/Move import stores files under the library root. Link keeps the original location. "
            "A custom PDF reader is used only for PDF attachments."
        )
        tip.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addLayout(form)
        layout.addWidget(tip)
        layout.addWidget(buttons)

    def value(self) -> AppSettings:
        return AppSettings(
            library_root=self.library_root_edit.text().strip(),
            default_import_mode=str(self.import_mode_combo.currentData()),
            recent_export_dir=self._original.recent_export_dir,
            pdf_reader_path=self.pdf_reader_edit.text().strip(),
            ui_theme=str(self.theme_combo.currentData()),
        )

    def _browse_library_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select library root")
        if selected:
            self.library_root_edit.setText(selected)

    def _browse_pdf_reader(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select PDF reader",
            filter="Executable (*.exe);;All files (*.*)",
        )
        if selected:
            self.pdf_reader_edit.setText(selected)
