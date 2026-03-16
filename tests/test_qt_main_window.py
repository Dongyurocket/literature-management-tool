import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController
from literature_manager.views.dialogs.settings_dialog import SettingsDialog
from literature_manager.viewmodels import MainWindowViewModel
from literature_manager.views.main_window import QtMainWindow


APP = QApplication.instance() or QApplication([])


@contextmanager
def build_window(root: Path):
    patcher = mock.patch.dict("os.environ", {"LITERATURE_MANAGER_HOME": str(root / "app_home")})
    patcher.start()
    controller = None
    window = None
    try:
        store = SettingsStore()
        settings = AppSettings(library_root=str(root / "library"))
        store.save(settings)
        controller = LibraryController(store, settings, auto_rebuild_index=False)
        window = QtMainWindow(MainWindowViewModel(controller))
        window.show()
        APP.processEvents()
        yield window
    finally:
        if window is not None:
            window.close()
            APP.processEvents()
        if controller is not None:
            controller.close()
        patcher.stop()


@contextmanager
def run_async_immediately(window: QtMainWindow):
    with mock.patch.object(window._thread_pool, "start", side_effect=lambda worker: worker.run()):
        yield


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    APP.processEvents()
    if not predicate():
        raise AssertionError("condition not met before timeout")


class QtMainWindowAsyncTests(unittest.TestCase):
    def test_async_task_resets_busy_status_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                with run_async_immediately(window):
                    window._run_async_task(
                        label="正在下载更新包…",
                        task=lambda: "ok",
                    )
                self.assertEqual(window.busy_label.text(), "就绪")
                self.assertNotIn("正在下载更新包", window.statusBar().currentMessage())

    def test_refresh_literature_list_provides_feedback_and_keeps_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                literature_id = window.viewmodel.controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "刷新测试",
                        "authors": ["Alice"],
                        "tags": [],
                    }
                )
                window._refresh_after_library_change(preserve_id=literature_id, navigation_key="all")
                toasts: list[tuple[str, str, str]] = []

                def capture_toast(title: str, message: str, *, level: str = "info", duration_ms: int = 3200) -> None:
                    toasts.append((title, message, level))

                window._show_toast = capture_toast  # type: ignore[method-assign]
                with run_async_immediately(window):
                    window._refresh_literature_list()

                self.assertEqual(window.busy_label.text(), "就绪")
                self.assertEqual(window._current_literature_id, literature_id)
                self.assertTrue(any(title == "列表已刷新" for title, _message, _level in toasts))

    def test_busy_state_is_cleared_before_result_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                observed: dict[str, str] = {}

                def capture_callback(_result) -> None:
                    observed["busy_label"] = window.busy_label.text()
                    observed["status_text"] = window.statusBar().currentMessage()

                with run_async_immediately(window):
                    window._run_async_task(
                        label="正在检查 GitHub 更新…",
                        task=lambda: {"ok": True},
                        on_result=capture_callback,
                    )
                self.assertEqual(observed.get("busy_label"), "就绪")
                self.assertNotIn("正在检查 GitHub 更新", observed.get("status_text", ""))

    def test_nested_busy_tasks_keep_latest_message_until_all_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                preview_token = window._begin_busy_task("正在生成 PDF 重命名预览…")
                rename_token = window._begin_busy_task("正在重命名 PDF 文件…")
                window._end_busy_task(preview_token)

                self.assertFalse(window.busy_progress.isHidden())
                self.assertEqual(window.busy_label.text(), "正在重命名 PDF 文件…")
                self.assertIn("正在重命名 PDF 文件", window.statusBar().currentMessage())

                window._end_busy_task(rename_token)

                self.assertTrue(window.busy_progress.isHidden())
                self.assertEqual(window.busy_label.text(), "就绪")
                self.assertNotIn("正在重命名 PDF 文件", window.statusBar().currentMessage())

    def test_async_task_callback_error_does_not_leave_busy_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                toasts: list[tuple[str, str, str]] = []

                def capture_toast(title: str, message: str, *, level: str = "info", duration_ms: int = 3200) -> None:
                    toasts.append((title, message, level))

                def broken_callback(_result) -> None:
                    raise RuntimeError("界面回调失败")

                window._show_toast = capture_toast  # type: ignore[method-assign]
                with run_async_immediately(window):
                    window._run_async_task(
                        label="正在检查 GitHub 更新…",
                        task=lambda: {"ok": True},
                        on_result=broken_callback,
                        error_title="检查更新失败",
                    )

                self.assertEqual(window.busy_label.text(), "就绪")
                self.assertTrue(
                    any(
                        title == "检查更新失败" and "界面回调失败" in message and level == "error"
                        for title, message, level in toasts
                    )
                )

    def test_async_task_finished_callback_error_is_also_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                toasts: list[tuple[str, str, str]] = []

                def capture_toast(title: str, message: str, *, level: str = "info", duration_ms: int = 3200) -> None:
                    toasts.append((title, message, level))

                def broken_finished() -> None:
                    raise RuntimeError("收尾失败")

                window._show_toast = capture_toast  # type: ignore[method-assign]
                with run_async_immediately(window):
                    window._run_async_task(
                        label="正在检查 GitHub 更新…",
                        task=lambda: {"ok": True},
                        on_finished=broken_finished,
                        error_title="检查更新失败",
                    )

                self.assertEqual(window.busy_label.text(), "就绪")
                self.assertTrue(
                    any(
                        title == "检查更新失败" and "收尾失败" in message and level == "error"
                        for title, message, level in toasts
                    )
                )

    def test_stale_busy_state_guard_recovers_when_no_active_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                token = window._begin_busy_task("正在检查 GitHub 更新…")
                window._busy_task_started_at[token] = time.monotonic() - 10
                with mock.patch.object(window._thread_pool, "activeThreadCount", return_value=0):
                    window._recover_stale_busy_state()
                self.assertEqual(window.busy_label.text(), "就绪")
                self.assertFalse(window._busy_tasks)


class QtMainWindowMetadataTests(unittest.TestCase):
    def test_cite_key_can_be_edited_and_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                literature_id = window.viewmodel.controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "引用键测试",
                        "authors": ["张三"],
                        "tags": [],
                    }
                )
                window._refresh_after_library_change(preserve_id=literature_id, navigation_key="all")
                window._show_detail(literature_id)
                self.assertFalse(window.cite_key_edit.isReadOnly())
                window.cite_key_edit.setText("ManualKey2026")
                window._save_metadata_changes()
                detail = window.viewmodel.controller.get_literature(literature_id)
                self.assertEqual(detail.get("cite_key"), "ManualKey2026")

    def test_unchanged_metadata_does_not_trigger_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                literature_id = window.viewmodel.controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "脏检查测试",
                        "authors": ["张三"],
                        "tags": [],
                    }
                )
                window._refresh_after_library_change(preserve_id=literature_id, navigation_key="all")
                window._show_detail(literature_id)

                with mock.patch.object(window.viewmodel, "save_metadata", wraps=window.viewmodel.save_metadata) as save_metadata:
                    window._save_metadata_changes()

                self.assertFalse(save_metadata.called)

    def test_entry_type_switch_updates_visible_fields_and_clears_hidden_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                literature_id = window.viewmodel.controller.save_literature(
                    {
                        "entry_type": "book",
                        "title": "字段裁剪测试",
                        "authors": ["张三"],
                        "publisher": "科学出版社",
                        "publication_place": "北京",
                        "edition": "2",
                        "tags": [],
                    }
                )
                window._refresh_after_library_change(preserve_id=literature_id, navigation_key="all")
                window._show_detail(literature_id)

                self.assertFalse(window.publisher_edit.isHidden())
                self.assertTrue(window.volume_edit.isHidden())

                window.entry_type_combo.setCurrentIndex(window.entry_type_combo.findData("journal_article"))
                APP.processEvents()
                window._save_metadata_changes()

                self.assertTrue(window.publisher_edit.isHidden())
                self.assertFalse(window.volume_edit.isHidden())
                detail = window.viewmodel.controller.get_literature(literature_id)
                self.assertEqual(detail.get("entry_type"), "journal_article")
                self.assertEqual(detail.get("publisher"), "")
                self.assertEqual(detail.get("publication_place"), "")
                self.assertEqual(detail.get("edition"), "")

    def test_manual_save_mode_marks_dirty_and_can_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                literature_id = window.viewmodel.controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "手动保存测试",
                        "authors": ["张三"],
                        "tags": [],
                    }
                )
                window._refresh_after_library_change(preserve_id=literature_id, navigation_key="all")
                window._show_detail(literature_id)

                window.metadata_autosave_checkbox.setChecked(False)
                APP.processEvents()
                self.assertFalse(window.viewmodel.settings.detail_autosave_enabled)

                window.title_edit.setText("手动保存测试 - 已修改")
                window._schedule_metadata_save()

                self.assertFalse(window._metadata_save_timer.isActive())
                self.assertEqual(window.metadata_save_label.text(), "未保存")

                window._save_metadata_changes()

                detail = window.viewmodel.controller.get_literature(literature_id)
                self.assertEqual(detail.get("title"), "手动保存测试 - 已修改")
                self.assertEqual(window.metadata_save_label.text(), "已保存")

    def test_switching_record_flushes_pending_metadata_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                first_id = window.viewmodel.controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "第一条",
                        "authors": ["张三"],
                        "tags": [],
                    }
                )
                second_id = window.viewmodel.controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "第二条",
                        "authors": ["李四"],
                        "tags": [],
                    }
                )
                window._refresh_after_library_change(preserve_id=first_id, navigation_key="all")
                window._show_detail(first_id)
                window.metadata_autosave_checkbox.setChecked(False)
                APP.processEvents()

                window.title_edit.setText("第一条 - 切换前保存")
                window._schedule_metadata_save()
                window._show_detail(second_id)

                detail = window.viewmodel.controller.get_literature(first_id)
                self.assertEqual(detail.get("title"), "第一条 - 切换前保存")
                self.assertEqual(window._current_literature_id, second_id)


class QtMainWindowTablePreferenceTests(unittest.TestCase):
    def test_custom_columns_and_widths_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_window(Path(tmp)) as window:
                window._save_table_preferences(
                    column_keys=["title", "authors", "note_count"],
                    column_widths={"title": 410, "authors": 240, "note_count": 92},
                )
                window._apply_table_preferences()

                self.assertEqual(window._table_model.column_keys(), ["title", "authors", "note_count"])
                self.assertEqual(window._table_model.headerData(2, Qt.Orientation.Horizontal), "笔记数")
                self.assertEqual(window.table.columnWidth(0), 410)

                window.table.setColumnWidth(1, 280)
                window._persist_current_table_layout()

                self.assertEqual(window.viewmodel.settings.list_column_widths["authors"], 280)


class SettingsDialogMetadataSourceTests(unittest.TestCase):
    def test_metadata_source_supports_multi_select_and_preserves_order(self):
        dialog = SettingsDialog(
            AppSettings(metadata_sources=["openalex", "cnki"]),
            parent=None,
        )
        self.assertTrue(dialog.metadata_source_checks["openalex"].isChecked())
        self.assertTrue(dialog.metadata_source_checks["cnki"].isChecked())
        dialog.metadata_source_checks["crossref"].setChecked(True)
        self.assertEqual(dialog.value().metadata_sources, ["openalex", "cnki", "crossref"])
        dialog.metadata_source_checks["openalex"].setChecked(False)
        self.assertEqual(dialog.value().metadata_sources, ["cnki", "crossref"])
        dialog.close()

    def test_settings_dialog_uses_human_readable_chinese_labels(self):
        dialog = SettingsDialog(AppSettings(), parent=None)
        self.assertEqual(dialog.windowTitle(), "\u8bbe\u7f6e")
        self.assertEqual(dialog.sync_mode_check.text(), "\u542f\u7528\u8de8\u8bbe\u5907\u540c\u6b65\u53cb\u597d\u6a21\u5f0f")
        self.assertEqual(dialog.metadata_source_checks["cnki"].text(), "\u77e5\u7f51\uff08\u4e2d\u6587\u6587\u732e\uff09")
        dialog.close()

    def test_sync_mode_hides_link_import_mode(self):
        dialog = SettingsDialog(
            AppSettings(default_import_mode="link", sync_mode_enabled=True),
            workspace_dir="C:\\sync-workspace",
            parent=None,
        )
        modes = [dialog.import_mode_combo.itemData(index) for index in range(dialog.import_mode_combo.count())]
        self.assertNotIn("link", modes)
        self.assertEqual(dialog.value().default_import_mode, "copy")
        self.assertEqual(dialog.workspace_dir(), "C:\\sync-workspace")
        dialog.close()


if __name__ == "__main__":
    unittest.main()
