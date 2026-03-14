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


if __name__ == "__main__":
    unittest.main()
