import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController
from literature_manager.viewmodels import MainWindowViewModel


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


class MainWindowViewModelTests(unittest.TestCase):
    def test_create_save_and_delete_literature_routes_through_viewmodel(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                viewmodel = MainWindowViewModel(controller)

                literature_id = viewmodel.create_new_literature()
                saved = viewmodel.save_metadata(
                    literature_id,
                    {
                        "title": "  视图模型保存测试  ",
                        "authors": ["张三", "李四"],
                        "year": "2026",
                        "reading_status": "在读",
                    },
                )

                self.assertEqual(saved["title"], "视图模型保存测试")
                self.assertEqual(saved["authors"], ["张三", "李四"])
                self.assertEqual(saved["year"], 2026)
                self.assertEqual(saved["reading_status"], "在读")

                viewmodel.delete_literature(literature_id)
                self.assertEqual(viewmodel.detail_payload(literature_id), {})

    def test_navigation_sections_include_dynamic_subjects_and_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Qt Navigation",
                        "year": 2026,
                        "subject": "Human Computer Interaction",
                        "rating": 5,
                        "authors": ["Alice Example"],
                        "tags": ["qt", "prototype"],
                        "reading_status": "在读",
                    }
                )

                viewmodel = MainWindowViewModel(controller)
                sections = viewmodel.navigation_sections()

                self.assertIn("主题", sections)
                self.assertTrue(any(item.label == "Human Computer Interaction" for item in sections["主题"]))
                self.assertIn("标签", sections)
                self.assertTrue(any(item.label == "qt" for item in sections["标签"]))
                self.assertTrue(any(item.key == "favorites" and item.count == 1 for item in sections["文库"]))

    def test_list_rows_applies_tag_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Tagged Result",
                        "year": 2026,
                        "authors": ["Alice Example"],
                        "tags": ["focus"],
                    }
                )
                controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Another Result",
                        "year": 2025,
                        "authors": ["Bob Example"],
                        "tags": ["other"],
                    }
                )

                viewmodel = MainWindowViewModel(controller)
                rows = viewmodel.list_rows(filters={"tag": "focus"})

                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].title, "Tagged Result")

    def test_metadata_lines_handle_empty_optional_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                literature_id = controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Sparse Record",
                        "authors": ["Alice Example"],
                        "tags": [],
                    }
                )

                viewmodel = MainWindowViewModel(controller)
                lines = viewmodel.metadata_lines(literature_id)

                self.assertTrue(all(isinstance(line, str) for line in lines))
                self.assertTrue(any(line.startswith("标题：") for line in lines))


if __name__ == "__main__":
    unittest.main()
