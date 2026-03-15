import tempfile
import unittest
import zipfile
from pathlib import Path

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.db import LibraryDatabase
from literature_manager.dedupe_service import find_duplicate_groups, merge_literatures
from literature_manager.import_service import import_scanned_items, scan_import_sources
from literature_manager.maintenance_service import create_backup, find_missing_paths
from literature_manager.metadata_service import parse_bib_text, parse_ris_text


def write_minimal_pdf(path: Path, title: str = "Sample PDF") -> None:
    content = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    path.write_bytes(content)


class MetadataParsingTests(unittest.TestCase):
    def test_parse_bib_text(self):
        content = """
@article{Wang2024,
  author = {Wang Lei and Li Ming},
  title = {Deep Learning Survey},
  journal = {Journal of AI},
  year = {2024},
  doi = {10.1000/test}
}
"""
        entries = parse_bib_text(content)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Deep Learning Survey")
        self.assertEqual(entries[0]["doi"], "10.1000/test")

    def test_parse_bib_text_reads_extended_fields(self):
        content = """
@book{Wang2024Book,
  author = {Wang Lei},
  editor = {Zhao Qian},
  translator = {Li Ming},
  title = {知识组织导论},
  subtitle = {方法与实践},
  titleaddon = {Introduction to Knowledge Organization},
  publisher = {科学出版社},
  location = {北京},
  edition = {2},
  year = {2024},
  month = {03},
  day = {15},
  urldate = {2026-03-15}
}
"""
        entries = parse_bib_text(content)
        self.assertEqual(entries[0]["entry_type"], "book")
        self.assertEqual(entries[0]["subtitle"], "方法与实践")
        self.assertEqual(entries[0]["translated_title"], "Introduction to Knowledge Organization")
        self.assertEqual(entries[0]["editors"], "Zhao Qian")
        self.assertEqual(entries[0]["translators"], "Li Ming")
        self.assertEqual(entries[0]["publication_place"], "北京")
        self.assertEqual(entries[0]["edition"], "2")

    def test_parse_ris_text(self):
        content = """
TY  - JOUR
AU  - Wang Lei
TI  - AI Survey
JO  - Journal of AI
PY  - 2023
DO  - 10.1000/abc
ER  -
"""
        entries = parse_ris_text(content)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entry_type"], "journal_article")
        self.assertEqual(entries[0]["title"], "AI Survey")

    def test_parse_ris_text_reads_extended_fields(self):
        content = """
TY  - THES
AU  - Wang Lei
A2  - Zhao Qian
A3  - Li Ming
TI  - Thesis Title
ST  - Subtitle
TT  - Translated Title
PB  - Tsinghua University
CY  - Beijing
DA  - 2024-03-15
M3  - Doctoral dissertation
Y2  - 2026-03-15
ID  - Wang2024Thesis
ER  -
"""
        entries = parse_ris_text(content)
        self.assertEqual(entries[0]["entry_type"], "thesis")
        self.assertEqual(entries[0]["subtitle"], "Subtitle")
        self.assertEqual(entries[0]["translated_title"], "Translated Title")
        self.assertEqual(entries[0]["school"], "Tsinghua University")
        self.assertEqual(entries[0]["publication_place"], "Beijing")
        self.assertEqual(entries[0]["degree"], "Doctoral dissertation")
        self.assertEqual(entries[0]["cite_key"], "Wang2024Thesis")


class WorkflowTests(unittest.TestCase):
    def test_import_scan_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "2024_AI_Survey.pdf"
            write_minimal_pdf(pdf)
            settings = AppSettings(library_root=str(root / "library"), default_import_mode="copy")
            db = LibraryDatabase(root / "library.sqlite3", lambda: settings.library_root)
            items = scan_import_sources([str(pdf)])
            result = import_scanned_items(db, items, settings)
            self.assertEqual(result["imported"], 1)
            rows = db._fetchall("SELECT COUNT(*) AS count FROM import_history")
            self.assertEqual(rows[0]["count"], 1)
            db.close()

    def test_dedupe_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = AppSettings(library_root=str(Path(tmp) / "library"))
            db = LibraryDatabase(Path(tmp) / "library.sqlite3", lambda: settings.library_root)
            first = db.save_literature(
                {
                    "entry_type": "journal_article",
                    "title": "AI Survey",
                    "year": 2024,
                    "doi": "10.1000/test",
                    "authors": ["Wang Lei"],
                    "tags": ["A"],
                }
            )
            second = db.save_literature(
                {
                    "entry_type": "journal_article",
                    "title": "AI Survey",
                    "year": 2024,
                    "doi": "10.1000/test",
                    "authors": ["Li Ming"],
                    "tags": ["B"],
                }
            )
            groups = find_duplicate_groups(db)
            self.assertEqual(len(groups), 1)
            merge_literatures(db, first, [second], "DOI")
            merged = db.get_literature(first)
            self.assertIn("Wang Lei", merged["authors"])
            self.assertIn("Li Ming", merged["authors"])
            self.assertIn("A", merged["tags"])
            self.assertIn("B", merged["tags"])
            self.assertIsNone(db.get_literature(second))
            db.close()

    def test_search_index_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings(library_root=str(root / "library"), default_import_mode="copy")
            db = LibraryDatabase(root / "library.sqlite3", lambda: settings.library_root)
            literature_id = db.save_literature(
                {
                    "entry_type": "journal_article",
                    "title": "机器学习综述",
                    "year": 2024,
                    "summary": "这是一篇关于大模型的综述。",
                    "authors": ["张三"],
                    "tags": [],
                }
            )
            note_path = root / "note.txt"
            note_path.write_text("包含检索关键词：知识图谱。", encoding="utf-8")
            db.save_note(
                literature_id=literature_id,
                title="外部笔记",
                content="",
                attachment_ids=[],
                note_type="file",
                note_format="text",
                external_file_path=str(note_path),
                import_mode="link",
            )
            db.rebuild_search_index()
            results = db.search_literatures("知识图谱")
            self.assertEqual(len(results), 1)
            note_path.unlink()
            missing = find_missing_paths(db)
            self.assertEqual(len(missing), 1)
            db.close()

    def test_backup_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            with tempfile.TemporaryDirectory() as app_home:
                store = SettingsStore()
                store.base_dir = Path(app_home)
                store.settings_path = Path(app_home) / "settings.json"
                store.database_path = Path(app_home) / "library.sqlite3"
                settings = AppSettings(library_root=str(home))
                store.save(settings)
                db = LibraryDatabase(store.database_path, lambda: settings.library_root)
                db.save_literature({"entry_type": "misc", "title": "测试", "authors": [], "tags": []})
                db.close()
                destination = Path(tmp) / "backup.zip"
                backup_path = create_backup(store, settings, str(destination))
                self.assertTrue(Path(backup_path).exists())
                with zipfile.ZipFile(backup_path) as archive:
                    self.assertIn("library.sqlite3", archive.namelist())
                    self.assertIn("settings.json", archive.namelist())


if __name__ == "__main__":
    unittest.main()
