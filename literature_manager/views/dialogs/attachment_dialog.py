from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from ...config import AppSettings
from ...utils import ROLE_LABELS, available_import_mode_labels


class AttachmentDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加附件")
        self.resize(420, 220)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.role_combo = QComboBox(self)
        for code, label in ROLE_LABELS.items():
            self.role_combo.addItem(label, code)
        form.addRow("附件角色", self.role_combo)

        self.language_edit = QLineEdit(self)
        form.addRow("语言", self.language_edit)

        self.import_mode_combo = QComboBox(self)
        for code, label in available_import_mode_labels(settings.sync_mode_enabled).items():
            self.import_mode_combo.addItem(label, code)
        self.import_mode_combo.setCurrentIndex(
            max(0, self.import_mode_combo.findData(settings.default_import_mode))
        )
        form.addRow("导入方式", self.import_mode_combo)

        self.primary_check = QCheckBox("设为该角色的主附件", self)
        self.primary_check.setChecked(True)
        form.addRow("", self.primary_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addLayout(form)
        layout.addWidget(buttons)

    def value(self) -> dict[str, object]:
        return {
            "role": str(self.role_combo.currentData()),
            "language": self.language_edit.text().strip(),
            "import_mode": str(self.import_mode_combo.currentData()),
            "is_primary": self.primary_check.isChecked(),
        }
