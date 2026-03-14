import tempfile
import unittest
from pathlib import Path

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController
from literature_manager.viewmodels import MainWindowViewModel


def build_controller(root: Path) -> LibraryController:
    store = SettingsStore()
    store.base_dir = root / "app_home"
    store.base_dir.mkdir(parents=True, exist_ok=True)
    store.settings_path = store.base_dir / "settings.json"
    store.database_path = store.base_dir / "library.sqlite3"
    settings = AppSettings(library_root=str(root / "library"))
    store.save(settings)
    return LibraryController(store, settings, auto_rebuild_index=False)


class MainWindowViewModelTests(unittest.TestCase):
    def test_navigation_sections_include_dynamic_subjects_and_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = build_controller(Path(tmp))
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

            self.assertIn("Subjects", sections)
            self.assertTrue(any(item.label == "Human Computer Interaction" for item in sections["Subjects"]))
            self.assertIn("Tags", sections)
            self.assertTrue(any(item.label == "qt" for item in sections["Tags"]))
            self.assertTrue(any(item.key == "favorites" and item.count == 1 for item in sections["Library"]))
            controller.close()

    def test_list_rows_applies_tag_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = build_controller(Path(tmp))
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
            controller.close()

    def test_metadata_lines_handle_empty_optional_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = build_controller(Path(tmp))
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
            controller.close()


if __name__ == "__main__":
    unittest.main()
