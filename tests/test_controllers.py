import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from literature_manager.config import AppSettings, SettingsStore, resolve_app_home, resolve_app_home_locator_path
from literature_manager.controllers import LibraryController
from literature_manager.utils import now_text


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
    def test_settings_store_persists_profile_relative_library_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"APPDATA": str(root / "appdata")}, clear=True):
                store = SettingsStore()
                profile_dir = store.settings_path.parent.resolve()
                settings = AppSettings(
                    library_root=str(profile_dir / "library_files"),
                    sync_mode_enabled=True,
                )
                store.save(settings)

                payload = json.loads(store.settings_path.read_text(encoding="utf-8"))
                loaded = store.load()

                self.assertEqual(payload["library_root"], "library_files")
                self.assertTrue(loaded.sync_mode_enabled)
                self.assertEqual(loaded.library_root, str((profile_dir / "library_files").resolve()))

    def test_settings_store_can_relocate_workspace_with_locator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"APPDATA": str(root / "appdata")}, clear=True):
                store = SettingsStore()
                store.save(AppSettings(library_root=str(store.settings_path.parent / "library_files")))

                target = root / "sync-workspace"
                store.relocate_base_dir(target)

                locator_payload = json.loads(resolve_app_home_locator_path().read_text(encoding="utf-8"))
                reopened = SettingsStore()

                self.assertEqual(locator_payload["app_home"], str(target.resolve()))
                self.assertEqual(resolve_app_home(), target.resolve())
                self.assertEqual(reopened.base_dir, target.resolve())

    def test_database_enables_wal_and_list_counts_use_joined_aggregates(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                literature_id = controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "数据库优化测试",
                        "year": 2026,
                        "subject": "Database Systems",
                        "reading_status": "在读",
                        "authors": ["Alice"],
                        "tags": ["wal"],
                    }
                )

                controller.database.connection.execute(
                    "INSERT INTO attachments(literature_id, label, role, language, file_path, is_relative, is_primary, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (literature_id, "paper.pdf", "source", "zh", "/tmp/paper.pdf", 0, 1, now_text()),
                )
                controller.database.connection.execute(
                    "INSERT INTO notes(literature_id, title, note_type, note_format, content, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (literature_id, "Reading Notes", "text", "markdown", "summary", now_text(), now_text()),
                )
                controller.database.connection.commit()

                mode = controller.database.connection.execute("PRAGMA journal_mode").fetchone()[0]
                index_names = {
                    row[1]
                    for row in controller.database.connection.execute("PRAGMA index_list(literatures)").fetchall()
                }
                rows = controller.list_literatures(subject="Database Systems", reading_status="在读")

                self.assertEqual(str(mode).lower(), "wal")
                self.assertTrue(
                    {"idx_literatures_year", "idx_literatures_subject", "idx_literatures_reading_status"}.issubset(index_names)
                )
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["attachment_count"], 1)
                self.assertEqual(rows[0]["note_count"], 1)

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

    def test_merge_metadata_payload_promotes_entry_type_and_prunes_hidden_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                merged = controller.merge_metadata_payload(
                    {
                        "entry_type": "misc",
                        "title": "Imported Item",
                        "authors": ["Alice"],
                        "publisher": "",
                        "volume": "12",
                        "tags": [],
                    },
                    {
                        "entry_type": "book",
                        "publisher": "Science Press",
                        "publication_place": "Beijing",
                        "edition": "2",
                        "volume": "99",
                    },
                )
                self.assertEqual(merged["entry_type"], "book")
                self.assertEqual(merged["publisher"], "Science Press")
                self.assertEqual(merged["publication_place"], "Beijing")
                self.assertEqual(merged["edition"], "2")
                self.assertEqual(merged["volume"], "")

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
