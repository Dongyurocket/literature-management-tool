import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.db import LibraryDatabase
from literature_manager.utils import detect_note_format, load_note_preview


def write_minimal_docx(path: Path, text: str) -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


class ConfigTests(unittest.TestCase):
    def test_settings_store_persists_pdf_reader_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"LITERATURE_MANAGER_HOME": tmp}):
                store = SettingsStore()
                settings = AppSettings(
                    pdf_reader_path=r"C:\Apps\Reader\reader.exe",
                    ui_theme="dark",
                )
                store.save(settings)
                loaded = store.load()
                self.assertEqual(loaded.pdf_reader_path, settings.pdf_reader_path)
                self.assertEqual(loaded.ui_theme, "dark")


class NoteFeatureTests(unittest.TestCase):
    def test_detect_note_format(self):
        self.assertEqual(detect_note_format("note.docx"), "docx")
        self.assertEqual(detect_note_format("note.md"), "markdown")
        self.assertEqual(detect_note_format("note.txt"), "text")

    def test_load_note_preview_reads_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.docx"
            write_minimal_docx(path, "这是 docx 笔记")
            preview = load_note_preview(path)
            self.assertIn("Word", preview)
            self.assertIn("这是 docx 笔记", preview)

    def test_database_can_save_external_note_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library_root = root / "library"
            db = LibraryDatabase(root / "library.sqlite3", lambda: str(library_root))
            literature_id = db.save_literature(
                {
                    "entry_type": "journal_article",
                    "title": "测试文献",
                    "year": 2025,
                    "authors": ["张三"],
                    "tags": [],
                }
            )
            note_path = root / "outline.docx"
            write_minimal_docx(note_path, "外部笔记内容")

            note_id = db.save_note(
                literature_id=literature_id,
                title="阅读提纲",
                content="",
                attachment_ids=[],
                note_type="file",
                note_format="docx",
                external_file_path=str(note_path),
                import_mode="link",
            )

            note = db.get_note(note_id)
            self.assertIsNotNone(note)
            self.assertEqual(note["note_type"], "file")
            self.assertEqual(note["note_format"], "docx")
            self.assertEqual(Path(note["resolved_path"]), note_path.resolve())
            db.close()

    def test_database_preserves_markdown_text_note_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = LibraryDatabase(root / "library.sqlite3", lambda: str(root / "library"))
            literature_id = db.save_literature(
                {
                    "entry_type": "journal_article",
                    "title": "Markdown Note",
                    "authors": ["张三"],
                    "tags": [],
                }
            )

            note_id = db.save_note(
                literature_id=literature_id,
                title="研究记录",
                content="# Heading",
                attachment_ids=[],
                note_type="text",
                note_format="markdown",
            )

            note = db.get_note(note_id)
            self.assertEqual(note["note_type"], "text")
            self.assertEqual(note["note_format"], "markdown")
            db.close()


if __name__ == "__main__":
    unittest.main()
