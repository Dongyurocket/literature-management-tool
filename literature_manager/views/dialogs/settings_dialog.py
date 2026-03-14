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
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...config import AppSettings, DEFAULT_METADATA_SOURCES, DEFAULT_UMI_OCR_REPO
from ...export_service import list_export_templates
from ...utils import IMPORT_MODE_LABELS

METADATA_SOURCE_LABELS = {
    "crossref": "Crossref（DOI）",
    "datacite": "DataCite（DOI）",
    "openalex": "OpenAlex（DOI / 标题）",
    "cnki": "知网（中文文章）",
    "ustc_openurl": "中科大图书馆 OpenURL",
    "tsinghua_openurl": "清华图书馆 OpenURL",
    "openlibrary": "OpenLibrary（ISBN）",
    "googlebooks": "Google Books（ISBN）",
}


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self._original = settings
        self.request_install_umi = False
        self.setWindowTitle("设置")
        self.resize(760, 620)

        layout = QVBoxLayout(self)

        basic_form = QFormLayout()
        basic_form.setContentsMargins(12, 12, 12, 12)
        basic_form.setSpacing(12)

        self.library_root_edit = QLineEdit(settings.library_root, self)
        library_row = QWidget(self)
        library_layout = QHBoxLayout(library_row)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.addWidget(self.library_root_edit, stretch=1)
        browse_library = QPushButton("浏览", self)
        browse_library.clicked.connect(self._browse_library_root)
        library_layout.addWidget(browse_library)
        basic_form.addRow("文库目录", library_row)

        self.import_mode_combo = QComboBox(self)
        for code, label in IMPORT_MODE_LABELS.items():
            self.import_mode_combo.addItem(label, code)
        self.import_mode_combo.setCurrentIndex(
            max(0, self.import_mode_combo.findData(settings.default_import_mode))
        )
        basic_form.addRow("默认导入方式", self.import_mode_combo)

        self.pdf_reader_edit = QLineEdit(settings.pdf_reader_path, self)
        reader_row = QWidget(self)
        reader_layout = QHBoxLayout(reader_row)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        reader_layout.addWidget(self.pdf_reader_edit, stretch=1)
        browse_reader = QPushButton("浏览", self)
        browse_reader.clicked.connect(self._browse_pdf_reader)
        reader_layout.addWidget(browse_reader)
        basic_form.addRow("PDF 阅读器", reader_row)

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(settings.ui_theme)))
        basic_form.addRow("界面主题", self.theme_combo)

        self.export_template_combo = QComboBox(self)
        for key, label in list_export_templates().items():
            self.export_template_combo.addItem(label, key)
        self.export_template_combo.setCurrentIndex(
            max(0, self.export_template_combo.findData(settings.preferred_export_template))
        )
        basic_form.addRow("默认导出模板", self.export_template_combo)

        layout.addLayout(basic_form)

        ocr_group = QGroupBox("OCR / 扫描版 PDF")
        ocr_layout = QFormLayout(ocr_group)
        ocr_layout.setSpacing(10)

        self.umi_ocr_path_edit = QLineEdit(settings.umi_ocr_path, self)
        ocr_path_row = QWidget(self)
        ocr_path_layout = QHBoxLayout(ocr_path_row)
        ocr_path_layout.setContentsMargins(0, 0, 0, 0)
        ocr_path_layout.addWidget(self.umi_ocr_path_edit, stretch=1)
        browse_ocr = QPushButton("浏览", self)
        browse_ocr.clicked.connect(self._browse_ocr_app)
        ocr_path_layout.addWidget(browse_ocr)
        install_ocr = QPushButton("下载安装", self)
        install_ocr.clicked.connect(self._request_install_umi)
        ocr_path_layout.addWidget(install_ocr)
        ocr_layout.addRow("Umi-OCR 程序", ocr_path_row)

        self.umi_ocr_variant_combo = QComboBox(self)
        self.umi_ocr_variant_combo.addItem("Rapid（默认，体积更小）", "rapid")
        self.umi_ocr_variant_combo.addItem("Paddle（更大，兼容性更高）", "paddle")
        self.umi_ocr_variant_combo.setCurrentIndex(
            max(0, self.umi_ocr_variant_combo.findData(settings.umi_ocr_variant))
        )
        ocr_layout.addRow("自动安装版本", self.umi_ocr_variant_combo)

        self.umi_ocr_repo_edit = QLineEdit(settings.umi_ocr_repo or DEFAULT_UMI_OCR_REPO, self)
        ocr_layout.addRow("Umi-OCR 仓库", self.umi_ocr_repo_edit)

        self.umi_ocr_command_edit = QTextEdit(self)
        self.umi_ocr_command_edit.setFixedHeight(80)
        self.umi_ocr_command_edit.setPlainText(settings.umi_ocr_command)
        self.umi_ocr_command_edit.setPlaceholderText(
            '示例："{umi_ocr}" --input "{input}" --output "{output}"'
        )
        ocr_layout.addRow("自定义命令模板", self.umi_ocr_command_edit)

        self.umi_ocr_timeout_spin = QSpinBox(self)
        self.umi_ocr_timeout_spin.setRange(30, 1800)
        self.umi_ocr_timeout_spin.setValue(int(settings.umi_ocr_timeout_sec or 180))
        self.umi_ocr_timeout_spin.setSuffix(" 秒")
        ocr_layout.addRow("OCR 超时", self.umi_ocr_timeout_spin)

        ocr_tip = QLabel(
            "如果命令模板留空，程序会自动启动并调用已安装的 Umi-OCR HTTP 接口。"
            "如需改用你自己的脚本，也可以填写支持 {umi_ocr}、{input}、{output} 三个占位符的命令模板。"
        )
        ocr_tip.setWordWrap(True)
        ocr_layout.addRow("", ocr_tip)
        layout.addWidget(ocr_group)

        update_group = QGroupBox("更新与元数据")
        update_layout = QVBoxLayout(update_group)
        update_form = QFormLayout()

        self.update_repo_edit = QLineEdit(settings.update_repo, self)
        update_form.addRow("GitHub 仓库", self.update_repo_edit)
        update_layout.addLayout(update_form)

        sources_box = QGroupBox("元数据回退顺序")
        sources_layout = QGridLayout(sources_box)
        self.metadata_source_checks: dict[str, QCheckBox] = {}
        self._syncing_metadata_source_checks = False
        configured_sources = [item for item in (settings.metadata_sources or []) if item in DEFAULT_METADATA_SOURCES]
        selected_source = configured_sources[0] if configured_sources else DEFAULT_METADATA_SOURCES[0]
        for index, source_key in enumerate(DEFAULT_METADATA_SOURCES):
            checkbox = QCheckBox(METADATA_SOURCE_LABELS[source_key], self)
            checkbox.setChecked(source_key == selected_source)
            checkbox.toggled.connect(
                lambda checked, key=source_key: self._on_metadata_source_toggled(key, checked)
            )
            self.metadata_source_checks[source_key] = checkbox
            sources_layout.addWidget(checkbox, index // 2, index % 2)
        update_layout.addWidget(sources_box)
        layout.addWidget(update_group)

        tip = QLabel(
            "复制/移动导入会把文件存入文库目录；仅关联会保留原始位置。"
            "自定义 PDF 阅读器仅在打开 PDF 附件时生效。"
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
            key for key, checkbox in self.metadata_source_checks.items() if checkbox.isChecked()
        ] or [DEFAULT_METADATA_SOURCES[0]]
        return AppSettings(
            library_root=self.library_root_edit.text().strip(),
            default_import_mode=str(self.import_mode_combo.currentData()),
            recent_export_dir=self._original.recent_export_dir,
            pdf_reader_path=self.pdf_reader_edit.text().strip(),
            ui_theme=str(self.theme_combo.currentData()),
            umi_ocr_path=self.umi_ocr_path_edit.text().strip(),
            umi_ocr_repo=self.umi_ocr_repo_edit.text().strip() or DEFAULT_UMI_OCR_REPO,
            umi_ocr_variant=str(self.umi_ocr_variant_combo.currentData()),
            umi_ocr_command=self.umi_ocr_command_edit.toPlainText().strip(),
            umi_ocr_timeout_sec=self.umi_ocr_timeout_spin.value(),
            update_repo=self.update_repo_edit.text().strip(),
            metadata_sources=selected_sources,
            preferred_export_template=str(self.export_template_combo.currentData()),
        )

    def _on_metadata_source_toggled(self, source_key: str, checked: bool) -> None:
        if self._syncing_metadata_source_checks:
            return
        if checked:
            self._syncing_metadata_source_checks = True
            try:
                for other_key, checkbox in self.metadata_source_checks.items():
                    if other_key != source_key and checkbox.isChecked():
                        checkbox.setChecked(False)
            finally:
                self._syncing_metadata_source_checks = False
            return

        if any(checkbox.isChecked() for checkbox in self.metadata_source_checks.values()):
            return
        self._syncing_metadata_source_checks = True
        try:
            self.metadata_source_checks[source_key].setChecked(True)
        finally:
            self._syncing_metadata_source_checks = False

    def _browse_library_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择文库目录")
        if selected:
            self.library_root_edit.setText(selected)

    def _browse_pdf_reader(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PDF 阅读器",
            filter="可执行文件 (*.exe);;所有文件 (*.*)",
        )
        if selected:
            self.pdf_reader_edit.setText(selected)

    def _browse_ocr_app(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Umi-OCR 程序",
            filter="可执行文件 (*.exe);;所有文件 (*.*)",
        )
        if selected:
            self.umi_ocr_path_edit.setText(selected)

    def _request_install_umi(self) -> None:
        self.request_install_umi = True
        self.accept()
