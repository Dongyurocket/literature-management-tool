import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
                        label="正在下载安装 Umi-OCR…",
                        task=lambda: {"ok": True},
                        on_result=broken_callback,
                        error_title="安装 Umi-OCR 失败",
                    )

                self.assertEqual(window.busy_label.text(), "就绪")
                self.assertTrue(
                    any(
                        title == "安装 Umi-OCR 失败" and "界面回调失败" in message and level == "error"
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


if __name__ == "__main__":
    unittest.main()
