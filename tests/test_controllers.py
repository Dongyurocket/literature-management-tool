import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController


@contextmanager
def build_controller(root: Path):
    patcher = mock.patch.dict("os.environ", {"LITERATURE_MANAGER_HOME": str(root / "app_home")})
    patcher.start()
    controller = None
    try:
        store = SettingsStore()
        settings = AppSettings(library_root=str(root / "library"))
        store.save(settings)
        controller = LibraryController(store, settings, auto_rebuild_index=False)
        yield controller
    finally:
        if controller is not None:
            controller.close()
        patcher.stop()


class LibraryControllerTests(unittest.TestCase):
    def test_merge_metadata_payload_appends_new_tags_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                merged = controller.merge_metadata_payload(
                    {
                        "title": "Existing Title",
                        "summary": "Keep me",
                        "authors": ["Alice"],
                        "tags": ["core"],
                    },
                    {
                        "title": "Incoming Title",
                        "summary": "Incoming Summary",
                        "authors": ["Bob"],
                        "tags": ["core", "new"],
                        "doi": "10.1000/example",
                    },
                )
                self.assertEqual(merged["title"], "Existing Title")
                self.assertEqual(merged["summary"], "Keep me")
                self.assertEqual(merged["authors"], ["Alice", "Bob"])
                self.assertEqual(merged["tags"], ["core", "new"])
                self.assertEqual(merged["doi"], "10.1000/example")

    def test_merge_metadata_payload_replaces_placeholder_title_and_merges_keywords(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                merged = controller.merge_metadata_payload(
                    {
                        "title": "未命名文献",
                        "authors": ["佚名"],
                        "keywords": "机器学习",
                        "tags": ["core"],
                    },
                    {
                        "title": "真实标题",
                        "authors": ["张三", "李四"],
                        "keywords": "机器学习；深度学习",
                        "tags": ["new"],
                    },
                )
                self.assertEqual(merged["title"], "真实标题")
                self.assertEqual(merged["authors"], ["张三", "李四"])
                self.assertEqual(merged["keywords"], "机器学习；深度学习")
                self.assertEqual(merged["tags"], ["core", "new"])

    def test_export_csl_json_updates_recent_export_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with build_controller(root) as controller:
                literature_id = controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Controller Export Test",
                        "year": 2026,
                        "authors": ["Alice Example"],
                        "tags": [],
                    }
                )
                destination = root / "exports" / "items.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                count = controller.export_csl_json([literature_id], str(destination))

                self.assertEqual(count, 1)
                payload = json.loads(destination.read_text(encoding="utf-8"))
                self.assertEqual(payload[0]["title"], "Controller Export Test")
                self.assertEqual(controller.settings.recent_export_dir, str(destination.parent.resolve()))

    def test_export_template_writes_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with build_controller(root) as controller:
                literature_id = controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "中文测试文献",
                        "year": 2025,
                        "authors": ["张三"],
                        "subject": "人工智能",
                        "tags": ["综述"],
                    }
                )
                destination = root / "exports" / "report.md"
                path = controller.export_template([literature_id], "markdown_report", str(destination))
                content = Path(path).read_text(encoding="utf-8")
                self.assertIn("中文测试文献", content)
                self.assertIn("人工智能", content)

    def test_profile_create_and_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with build_controller(root) as controller:
                created = controller.create_library_profile("归档文库")
                self.assertEqual(created["name"], "归档文库")

                switched = controller.switch_library_profile("归档文库")
                self.assertEqual(switched["name"], "归档文库")
                self.assertTrue(controller.settings.library_root.endswith("library_files"))


if __name__ == "__main__":
    unittest.main()
