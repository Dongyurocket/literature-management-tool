import json
import tempfile
import unittest
from pathlib import Path

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController


def build_controller(root: Path) -> LibraryController:
    store = SettingsStore()
    store.base_dir = root / "app_home"
    store.base_dir.mkdir(parents=True, exist_ok=True)
    store.settings_path = store.base_dir / "settings.json"
    store.database_path = store.base_dir / "library.sqlite3"
    settings = AppSettings(library_root=str(root / "library"))
    store.save(settings)
    return LibraryController(store, settings, auto_rebuild_index=False)


class LibraryControllerTests(unittest.TestCase):
    def test_merge_metadata_payload_appends_new_tags_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = build_controller(Path(tmp))
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
            self.assertEqual(merged["authors"], ["Alice"])
            self.assertEqual(merged["tags"], ["core", "new"])
            self.assertEqual(merged["doi"], "10.1000/example")
            controller.close()

    def test_export_csl_json_updates_recent_export_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = build_controller(root)
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
            controller.close()


if __name__ == "__main__":
    unittest.main()
