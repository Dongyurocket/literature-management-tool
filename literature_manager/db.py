from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .metadata_service import extract_pdf_text
from .utils import (
    build_attachment_name,
    build_bibtex,
    build_cite_key,
    build_storage_name,
    compute_checksum,
    detect_note_format,
    ensure_unique_path,
    load_note_content,
    now_text,
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS literatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type TEXT NOT NULL DEFAULT 'journal_article',
    title TEXT NOT NULL,
    translated_title TEXT,
    publication_title TEXT,
    publisher TEXT,
    school TEXT,
    conference_name TEXT,
    standard_number TEXT,
    patent_number TEXT,
    year INTEGER,
    month TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    doi TEXT,
    isbn TEXT,
    url TEXT,
    language TEXT,
    country TEXT,
    subject TEXT,
    keywords TEXT,
    summary TEXT,
    abstract TEXT,
    reading_status TEXT,
    rating INTEGER,
    remarks TEXT,
    cite_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_literatures_cite_key
    ON literatures(cite_key)
    WHERE cite_key IS NOT NULL AND cite_key <> '';

CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS literature_authors (
    literature_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    author_order INTEGER NOT NULL,
    PRIMARY KEY (literature_id, author_id, author_order),
    FOREIGN KEY (literature_id) REFERENCES literatures(id) ON DELETE CASCADE,
    FOREIGN KEY (author_id) REFERENCES authors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS literature_tags (
    literature_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (literature_id, tag_id),
    FOREIGN KEY (literature_id) REFERENCES literatures(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    literature_id INTEGER NOT NULL,
    label TEXT,
    role TEXT NOT NULL,
    language TEXT,
    file_path TEXT NOT NULL,
    is_relative INTEGER NOT NULL DEFAULT 0,
    is_primary INTEGER NOT NULL DEFAULT 0,
    original_name TEXT,
    file_size INTEGER NOT NULL DEFAULT 0,
    checksum TEXT,
    extracted_text TEXT,
    text_extracted_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (literature_id) REFERENCES literatures(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    literature_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    note_type TEXT NOT NULL DEFAULT 'text',
    note_format TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    external_path TEXT,
    external_is_relative INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (literature_id) REFERENCES literatures(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS note_attachment_links (
    note_id INTEGER NOT NULL,
    attachment_id INTEGER NOT NULL,
    PRIMARY KEY (note_id, attachment_id),
    FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rename_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attachment_id INTEGER NOT NULL,
    old_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    renamed_at TEXT NOT NULL,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    literature_id INTEGER NOT NULL,
    metadata_json TEXT,
    imported_at TEXT NOT NULL,
    FOREIGN KEY (literature_id) REFERENCES literatures(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS merge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_literature_id INTEGER NOT NULL,
    merged_literature_id INTEGER NOT NULL,
    merge_reason TEXT,
    details_json TEXT,
    merged_at TEXT NOT NULL
);
"""

LITERATURE_COLUMNS = [
    "entry_type",
    "title",
    "translated_title",
    "publication_title",
    "publisher",
    "school",
    "conference_name",
    "standard_number",
    "patent_number",
    "year",
    "month",
    "volume",
    "issue",
    "pages",
    "doi",
    "isbn",
    "url",
    "language",
    "country",
    "subject",
    "keywords",
    "summary",
    "abstract",
    "reading_status",
    "rating",
    "remarks",
    "cite_key",
]


class LibraryDatabase:
    def __init__(self, db_path: Path, library_root_getter) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._library_root_getter = library_root_getter
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self._apply_migrations()

    def close(self) -> None:
        self.connection.close()

    def _apply_migrations(self) -> None:
        self._ensure_column("notes", "note_type", "TEXT NOT NULL DEFAULT 'text'")
        self._ensure_column("notes", "note_format", "TEXT NOT NULL DEFAULT 'text'")
        self._ensure_column("notes", "external_path", "TEXT")
        self._ensure_column("notes", "external_is_relative", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("attachments", "original_name", "TEXT")
        self._ensure_column("attachments", "file_size", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("attachments", "extracted_text", "TEXT")
        self._ensure_column("attachments", "text_extracted_at", "TEXT")
        self._ensure_search_table()
        self.connection.execute("PRAGMA user_version = 3")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in self._fetchall(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_search_table(self) -> None:
        self.connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS literature_fts USING fts5("
            "literature_id UNINDEXED, title, translated_title, authors, subject, keywords, summary, abstract, notes_text, attachments_text)"
        )

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.connection.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def rebuild_search_index(self) -> None:
        self.connection.execute("DELETE FROM literature_fts")
        rows = self._fetchall("SELECT id FROM literatures ORDER BY id")
        for row in rows:
            self.refresh_search_index_for_literature(int(row["id"]))
        self.connection.commit()

    def refresh_search_index_for_literature(self, literature_id: int) -> None:
        literature = self.get_literature(literature_id)
        self.connection.execute("DELETE FROM literature_fts WHERE literature_id = ?", (literature_id,))
        if not literature:
            return
        authors = " ".join(literature.get("authors", []))
        notes_texts: list[str] = []
        for note in literature.get("notes", []):
            if note.get("note_type") == "file" and note.get("resolved_path"):
                notes_texts.append(load_note_content(note["resolved_path"]))
            else:
                notes_texts.append(note.get("content", ""))
        attachments_texts: list[str] = []
        for item in literature.get("attachments", []):
            extracted = item.get("extracted_text", "")
            resolved_path = item.get("resolved_path", "")
            if not extracted and resolved_path and Path(resolved_path).exists():
                extracted = self._extract_attachment_text(Path(resolved_path))
                if extracted:
                    self.connection.execute(
                        "UPDATE attachments SET extracted_text = ?, text_extracted_at = ? WHERE id = ?",
                        (extracted, now_text(), item["id"]),
                    )
            if extracted:
                attachments_texts.append(extracted)
        self.connection.execute(
            "INSERT INTO literature_fts(literature_id, title, translated_title, authors, subject, keywords, summary, abstract, notes_text, attachments_text) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                literature_id,
                literature.get("title", ""),
                literature.get("translated_title", ""),
                authors,
                literature.get("subject", ""),
                literature.get("keywords", ""),
                literature.get("summary", ""),
                literature.get("abstract", ""),
                "\n".join(notes_texts),
                "\n".join(attachments_texts),
            ),
        )

    def search_literatures(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        text = (query or "").strip()
        if not text:
            return []
        sql = (
            "SELECT l.id, l.title, l.year, l.entry_type, "
            "COALESCE((SELECT GROUP_CONCAT(a.name, ' / ') FROM literature_authors la JOIN authors a ON a.id = la.author_id "
            "WHERE la.literature_id = l.id ORDER BY la.author_order), '') AS authors_display, "
            "snippet(literature_fts, 6, '[', ']', '...', 12) AS summary_hit, "
            "snippet(literature_fts, 8, '[', ']', '...', 12) AS notes_hit, "
            "snippet(literature_fts, 9, '[', ']', '...', 12) AS attachment_hit "
            "FROM literature_fts "
            "JOIN literatures l ON l.id = literature_fts.literature_id "
            "WHERE literature_fts MATCH ? "
            "ORDER BY bm25(literature_fts) LIMIT ?"
        )
        try:
            rows = self._fetchall(sql, (text, limit))
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            like = f"%{text}%"
            fallback = self._fetchall(
                "SELECT id, title, year, entry_type FROM literatures WHERE title LIKE ? OR subject LIKE ? OR keywords LIKE ? OR summary LIKE ? LIMIT ?",
                (like, like, like, like, limit),
            )
            return [dict(row) for row in fallback]

    def get_statistics(self) -> dict[str, Any]:
        total = self._fetchone("SELECT COUNT(*) AS count FROM literatures")["count"]
        attachments = self._fetchone("SELECT COUNT(*) AS count FROM attachments")["count"]
        notes = self._fetchone("SELECT COUNT(*) AS count FROM notes")["count"]
        by_year = [dict(row) for row in self._fetchall("SELECT COALESCE(CAST(year AS TEXT), '未标注') AS label, COUNT(*) AS count FROM literatures GROUP BY COALESCE(year, -1) ORDER BY year DESC")]
        by_subject = [dict(row) for row in self._fetchall("SELECT COALESCE(subject, '未分类') AS label, COUNT(*) AS count FROM literatures GROUP BY COALESCE(subject, '未分类') ORDER BY count DESC, label LIMIT 12")]
        by_status = [dict(row) for row in self._fetchall("SELECT COALESCE(reading_status, '未标注') AS label, COUNT(*) AS count FROM literatures GROUP BY COALESCE(reading_status, '未标注') ORDER BY count DESC")]
        return {
            "total_literatures": total,
            "total_attachments": attachments,
            "total_notes": notes,
            "by_year": by_year,
            "by_subject": by_subject,
            "by_status": by_status,
        }

    def list_filter_values(self) -> dict[str, list[str]]:
        subjects = [row[0] for row in self._fetchall(
            "SELECT DISTINCT subject FROM literatures WHERE subject IS NOT NULL AND subject <> '' ORDER BY subject"
        )]
        years = [str(row[0]) for row in self._fetchall(
            "SELECT DISTINCT year FROM literatures WHERE year IS NOT NULL ORDER BY year DESC"
        )]
        entry_types = [row[0] for row in self._fetchall(
            "SELECT DISTINCT entry_type FROM literatures ORDER BY entry_type"
        )]
        tags = [row[0] for row in self._fetchall(
            "SELECT DISTINCT name FROM tags ORDER BY name"
        )]
        statuses = [row[0] for row in self._fetchall(
            "SELECT DISTINCT reading_status FROM literatures WHERE reading_status IS NOT NULL AND reading_status <> '' ORDER BY reading_status"
        )]
        return {
            "subjects": subjects,
            "years": years,
            "entry_types": entry_types,
            "tags": tags,
            "reading_statuses": statuses,
        }

    def list_literatures(
        self,
        *,
        search: str = "",
        subject: str = "",
        year: str = "",
        entry_type: str = "",
        tag: str = "",
        reading_status: str = "",
        min_rating: int = 0,
        created_after: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if search:
            like = f"%{search}%"
            clauses.append(
                "(" 
                "l.title LIKE ? OR l.translated_title LIKE ? OR l.subject LIKE ? OR l.keywords LIKE ? OR l.summary LIKE ? OR "
                "EXISTS (SELECT 1 FROM literature_authors la JOIN authors a ON a.id = la.author_id "
                "WHERE la.literature_id = l.id AND a.name LIKE ?)"
                ")"
            )
            params.extend([like, like, like, like, like, like])
        if subject:
            clauses.append("l.subject = ?")
            params.append(subject)
        if year:
            clauses.append("CAST(l.year AS TEXT) = ?")
            params.append(year)
        if entry_type:
            clauses.append("l.entry_type = ?")
            params.append(entry_type)
        if tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM literature_tags lt JOIN tags t ON t.id = lt.tag_id "
                "WHERE lt.literature_id = l.id AND t.name = ?)"
            )
            params.append(tag)
        if reading_status:
            clauses.append("COALESCE(l.reading_status, '') = ?")
            params.append(reading_status)
        if min_rating > 0:
            clauses.append("COALESCE(l.rating, 0) >= ?")
            params.append(min_rating)
        if created_after:
            clauses.append("l.created_at >= ?")
            params.append(created_after)

        sql = f"""
            SELECT
                l.*,
                COALESCE((
                    SELECT GROUP_CONCAT(a.name, ' / ')
                    FROM literature_authors la
                    JOIN authors a ON a.id = la.author_id
                    WHERE la.literature_id = l.id
                    ORDER BY la.author_order
                ), '') AS authors_display,
                COALESCE((SELECT COUNT(*) FROM attachments att WHERE att.literature_id = l.id), 0) AS attachment_count,
                COALESCE((
                    SELECT GROUP_CONCAT(t.name, ', ')
                    FROM literature_tags lt
                    JOIN tags t ON t.id = lt.tag_id
                    WHERE lt.literature_id = l.id
                ), '') AS tags_display,
                COALESCE((SELECT COUNT(*) FROM notes n WHERE n.literature_id = l.id), 0) AS note_count
            FROM literatures l
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(l.year, 0) DESC, l.updated_at DESC, l.id DESC
        """
        return [dict(row) for row in self._fetchall(sql, tuple(params))]

    def get_literature(self, literature_id: int) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM literatures WHERE id = ?", (literature_id,))
        if not row:
            return None
        payload = dict(row)
        payload["authors"] = self.get_authors(literature_id)
        payload["tags"] = self.get_tags(literature_id)
        payload["attachments"] = self.get_attachments(literature_id)
        payload["notes"] = self.list_notes(literature_id)
        return payload

    def get_authors(self, literature_id: int) -> list[str]:
        rows = self._fetchall(
            "SELECT a.name FROM literature_authors la "
            "JOIN authors a ON a.id = la.author_id "
            "WHERE la.literature_id = ? ORDER BY la.author_order",
            (literature_id,),
        )
        return [row[0] for row in rows]

    def get_tags(self, literature_id: int) -> list[str]:
        rows = self._fetchall(
            "SELECT t.name FROM literature_tags lt "
            "JOIN tags t ON t.id = lt.tag_id "
            "WHERE lt.literature_id = ? ORDER BY t.name",
            (literature_id,),
        )
        return [row[0] for row in rows]

    def save_literature(self, payload: dict[str, Any]) -> int:
        data = {column: payload.get(column) for column in LITERATURE_COLUMNS}
        authors = payload.get("authors", [])
        tags = payload.get("tags", [])
        if not data.get("cite_key"):
            data["cite_key"] = build_cite_key(authors, data.get("year"), data.get("title", ""))
        data["cite_key"] = self._ensure_unique_cite_key(data["cite_key"], payload.get("id"))
        now = now_text()
        literature_id = payload.get("id")

        if literature_id:
            assignments = ", ".join(f"{column} = ?" for column in LITERATURE_COLUMNS)
            values = [data[column] for column in LITERATURE_COLUMNS] + [now, literature_id]
            self.connection.execute(
                f"UPDATE literatures SET {assignments}, updated_at = ? WHERE id = ?",
                values,
            )
        else:
            placeholders = ", ".join("?" for _ in LITERATURE_COLUMNS)
            columns = ", ".join(LITERATURE_COLUMNS)
            values = [data[column] for column in LITERATURE_COLUMNS] + [now, now]
            cursor = self.connection.execute(
                f"INSERT INTO literatures ({columns}, created_at, updated_at) VALUES ({placeholders}, ?, ?)",
                values,
            )
            literature_id = int(cursor.lastrowid)

        self.connection.execute("DELETE FROM literature_authors WHERE literature_id = ?", (literature_id,))
        for index, author in enumerate(authors):
            author_id = self._get_or_create_author(author)
            self.connection.execute(
                "INSERT INTO literature_authors(literature_id, author_id, author_order) VALUES(?, ?, ?)",
                (literature_id, author_id, index),
            )

        self.connection.execute("DELETE FROM literature_tags WHERE literature_id = ?", (literature_id,))
        for tag in tags:
            tag_id = self._get_or_create_tag(tag)
            self.connection.execute(
                "INSERT INTO literature_tags(literature_id, tag_id) VALUES(?, ?)",
                (literature_id, tag_id),
            )

        self.refresh_search_index_for_literature(int(literature_id))
        self.connection.commit()
        return int(literature_id)

    def delete_literature(self, literature_id: int) -> None:
        self.connection.execute("DELETE FROM literatures WHERE id = ?", (literature_id,))
        self.connection.execute("DELETE FROM literature_fts WHERE literature_id = ?", (literature_id,))
        self.connection.commit()

    def _get_or_create_author(self, name: str) -> int:
        row = self._fetchone("SELECT id FROM authors WHERE name = ?", (name,))
        if row:
            return int(row["id"])
        cursor = self.connection.execute("INSERT INTO authors(name) VALUES(?)", (name,))
        return int(cursor.lastrowid)

    def _get_or_create_tag(self, name: str) -> int:
        row = self._fetchone("SELECT id FROM tags WHERE name = ?", (name,))
        if row:
            return int(row["id"])
        cursor = self.connection.execute("INSERT INTO tags(name) VALUES(?)", (name,))
        return int(cursor.lastrowid)

    def _ensure_unique_cite_key(self, cite_key: str, literature_id: int | None) -> str:
        key = cite_key or "UntitledKey"
        counter = 2
        while True:
            row = self._fetchone("SELECT id FROM literatures WHERE cite_key = ?", (key,))
            if not row or int(row["id"]) == (literature_id or 0):
                return key
            key = f"{cite_key}_{counter}"
            counter += 1

    def library_root(self) -> Path | None:
        root_text = self._library_root_getter()
        if not root_text:
            return None
        root = Path(root_text).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _store_path(self, path: Path) -> tuple[str, int]:
        path = path.resolve()
        root = self.library_root()
        if root:
            try:
                return str(path.relative_to(root.resolve())), 1
            except ValueError:
                pass
        return str(path), 0

    def resolve_path(self, stored_path: str, is_relative: int) -> Path:
        if is_relative:
            root = self.library_root()
            if not root:
                return Path(stored_path)
            return (root / stored_path).resolve()
        return Path(stored_path)

    def _extract_attachment_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return extract_pdf_text(path)
        if suffix in {".docx", ".md", ".markdown", ".txt"}:
            return load_note_content(path)
        return ""

    def add_attachments(
        self,
        literature_id: int,
        files: list[str],
        *,
        role: str,
        language: str,
        import_mode: str,
        is_primary: bool,
    ) -> list[int]:
        literature = self.get_literature(literature_id)
        if not literature:
            raise ValueError("文献不存在")

        root = self.library_root()
        if import_mode in {"copy", "move"} and not root:
            raise ValueError("请先配置文献库目录，再导入文件。")

        created_ids: list[int] = []
        folder_name = build_storage_name(literature.get("authors", []), literature.get("year"), literature["title"])
        target_dir = root / folder_name if root else None
        if target_dir:
            target_dir.mkdir(parents=True, exist_ok=True)

        if is_primary:
            self.connection.execute("UPDATE attachments SET is_primary = 0 WHERE literature_id = ? AND role = ?", (literature_id, role))

        for file_name in files:
            source = Path(file_name).expanduser().resolve()
            if not source.exists():
                continue
            final_path = source
            if import_mode in {"copy", "move"} and target_dir is not None:
                final_path = ensure_unique_path(target_dir / source.name)
                if import_mode == "copy":
                    shutil.copy2(source, final_path)
                else:
                    shutil.move(str(source), str(final_path))
            stored_path, is_relative = self._store_path(final_path)
            extracted_text = self._extract_attachment_text(final_path)
            cursor = self.connection.execute(
                "INSERT INTO attachments(literature_id, label, role, language, file_path, is_relative, is_primary, original_name, file_size, checksum, extracted_text, text_extracted_at, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    literature_id,
                    final_path.name,
                    role,
                    language,
                    stored_path,
                    is_relative,
                    1 if is_primary else 0,
                    source.name,
                    final_path.stat().st_size if final_path.exists() else 0,
                    compute_checksum(final_path),
                    extracted_text,
                    now_text() if extracted_text else "",
                    now_text(),
                ),
            )
            created_ids.append(int(cursor.lastrowid))
        self.connection.commit()
        self.refresh_search_index_for_literature(literature_id)
        self.connection.commit()
        return created_ids

    def get_attachments(self, literature_id: int) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM attachments WHERE literature_id = ? ORDER BY is_primary DESC, created_at DESC, id DESC",
            (literature_id,),
        )
        items = []
        for row in rows:
            payload = dict(row)
            payload["resolved_path"] = str(self.resolve_path(payload["file_path"], payload["is_relative"]))
            items.append(payload)
        return items

    def get_attachment(self, attachment_id: int) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM attachments WHERE id = ?", (attachment_id,))
        if not row:
            return None
        payload = dict(row)
        payload["resolved_path"] = str(self.resolve_path(payload["file_path"], payload["is_relative"]))
        return payload

    def delete_attachment(self, attachment_id: int, *, delete_file: bool) -> None:
        attachment = self.get_attachment(attachment_id)
        if not attachment:
            return
        path = Path(attachment["resolved_path"])
        self.connection.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        self.connection.commit()
        if delete_file and path.exists():
            path.unlink(missing_ok=True)
        self.refresh_search_index_for_literature(int(attachment["literature_id"]))
        self.connection.commit()

    def _prepare_note_file_storage(
        self,
        literature: dict[str, Any],
        file_path: str,
        import_mode: str,
    ) -> tuple[str, int, str, str]:
        source = Path(file_path).expanduser().resolve()
        if not source.exists():
            raise ValueError("笔记文件不存在")

        root = self.library_root()
        if import_mode in {"copy", "move"} and not root:
            raise ValueError("请先配置文献库目录，再导入笔记文件。")

        final_path = source
        if import_mode in {"copy", "move"} and root is not None:
            folder_name = build_storage_name(literature.get("authors", []), literature.get("year"), literature["title"])
            target_dir = root / folder_name / "notes"
            target_dir.mkdir(parents=True, exist_ok=True)
            final_path = ensure_unique_path(target_dir / source.name)
            if import_mode == "copy":
                shutil.copy2(source, final_path)
            else:
                shutil.move(str(source), str(final_path))

        stored_path, is_relative = self._store_path(final_path)
        return stored_path, is_relative, final_path.name, final_path.suffix.lower()

    def list_notes(self, literature_id: int) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT n.*, COALESCE((SELECT COUNT(*) FROM note_attachment_links nal WHERE nal.note_id = n.id), 0) AS attachment_count "
            "FROM notes n WHERE n.literature_id = ? ORDER BY n.updated_at DESC",
            (literature_id,),
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            if payload.get("external_path"):
                payload["resolved_path"] = str(
                    self.resolve_path(payload["external_path"], payload.get("external_is_relative", 0))
                )
            items.append(payload)
        return items

    def get_note(self, note_id: int) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM notes WHERE id = ?", (note_id,))
        if not row:
            return None
        payload = dict(row)
        linked = self._fetchall(
            "SELECT attachment_id FROM note_attachment_links WHERE note_id = ? ORDER BY attachment_id",
            (note_id,),
        )
        payload["attachment_ids"] = [int(item[0]) for item in linked]
        if payload.get("external_path"):
            payload["resolved_path"] = str(
                self.resolve_path(payload["external_path"], payload.get("external_is_relative", 0))
            )
        return payload

    def save_note(
        self,
        *,
        literature_id: int,
        title: str,
        content: str,
        attachment_ids: list[int],
        note_id: int | None = None,
        note_type: str = "text",
        note_format: str = "text",
        external_file_path: str = "",
        import_mode: str = "link",
    ) -> int:
        literature = self.get_literature(literature_id)
        if not literature:
            raise ValueError("文献不存在")
        now = now_text()
        external_path: str | None = None
        external_is_relative = 0
        if note_type == "file":
            if external_file_path:
                external_path, external_is_relative, inferred_title, suffix = self._prepare_note_file_storage(
                    literature,
                    external_file_path,
                    import_mode,
                )
                if not title.strip():
                    title = Path(inferred_title).stem
                if note_format in {"", "text"} and suffix:
                    note_format = detect_note_format(inferred_title)
            elif note_id:
                existing_note = self.get_note(note_id)
                if existing_note:
                    external_path = existing_note.get("external_path")
                    external_is_relative = existing_note.get("external_is_relative", 0)
                    note_format = existing_note.get("note_format", note_format)
            if not external_path:
                raise ValueError("请选择要关联的笔记文件。")
            content = content or ""
        else:
            note_format = note_format or "text"

        if note_id:
            self.connection.execute(
                "UPDATE notes SET title = ?, note_type = ?, note_format = ?, content = ?, external_path = ?, external_is_relative = ?, updated_at = ? WHERE id = ?",
                (title, note_type, note_format, content, external_path, external_is_relative, now, note_id),
            )
            self.connection.execute("DELETE FROM note_attachment_links WHERE note_id = ?", (note_id,))
        else:
            cursor = self.connection.execute(
                "INSERT INTO notes(literature_id, title, note_type, note_format, content, external_path, external_is_relative, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (literature_id, title, note_type, note_format, content, external_path, external_is_relative, now, now),
            )
            note_id = int(cursor.lastrowid)
        for attachment_id in attachment_ids:
            self.connection.execute(
                "INSERT INTO note_attachment_links(note_id, attachment_id) VALUES(?, ?)",
                (note_id, attachment_id),
            )
        self.refresh_search_index_for_literature(literature_id)
        self.connection.commit()
        return int(note_id)

    def delete_note(self, note_id: int, *, delete_file: bool = False) -> None:
        note = self.get_note(note_id)
        self.connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.connection.commit()
        if delete_file and note and note.get("resolved_path"):
            Path(note["resolved_path"]).unlink(missing_ok=True)
        if note:
            self.refresh_search_index_for_literature(int(note["literature_id"]))
            self.connection.commit()

    def record_import_history(self, source_path: str, source_kind: str, literature_id: int, metadata: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO import_history(source_path, source_kind, literature_id, metadata_json, imported_at) VALUES(?, ?, ?, ?, ?)",
            (source_path, source_kind, literature_id, json.dumps(metadata, ensure_ascii=False), now_text()),
        )
        self.connection.commit()

    def record_merge_history(
        self,
        primary_literature_id: int,
        merged_literature_id: int,
        merge_reason: str,
        details: dict[str, Any],
    ) -> None:
        self.connection.execute(
            "INSERT INTO merge_history(primary_literature_id, merged_literature_id, merge_reason, details_json, merged_at) VALUES(?, ?, ?, ?, ?)",
            (primary_literature_id, merged_literature_id, merge_reason, json.dumps(details, ensure_ascii=False), now_text()),
        )
        self.connection.commit()

    def export_bib(self, literature_ids: list[int], destination: str) -> int:
        entries = [self._build_bib_payload(literature_id) for literature_id in literature_ids]
        entries = [entry for entry in entries if entry]
        Path(destination).write_text(build_bibtex(entries), encoding="utf-8")
        return len(entries)

    def _build_bib_payload(self, literature_id: int) -> dict[str, Any] | None:
        literature = self.get_literature(literature_id)
        if not literature:
            return None
        return {key: literature.get(key) for key in LITERATURE_COLUMNS} | {"authors": literature.get("authors", [])}

    def preview_pdf_renames(self, literature_ids: list[int]) -> list[dict[str, Any]]:
        previews: list[dict[str, Any]] = []
        reserved_targets: set[str] = set()
        for literature_id in literature_ids:
            literature = self.get_literature(literature_id)
            if not literature:
                continue
            for attachment in literature.get("attachments", []):
                path = Path(attachment["resolved_path"])
                if path.suffix.lower() != ".pdf":
                    continue
                new_name = build_attachment_name(
                    literature.get("authors", []),
                    literature.get("year"),
                    literature.get("title", ""),
                    attachment.get("role", "source"),
                    path.suffix,
                )
                desired = path.with_name(new_name)
                if desired != path:
                    candidate = desired
                    counter = 2
                    while candidate.exists() or str(candidate) in reserved_targets:
                        candidate = desired.with_name(f"{desired.stem}_{counter}{desired.suffix}")
                        counter += 1
                    desired = candidate
                reserved_targets.add(str(desired))
                previews.append(
                    {
                        "attachment_id": attachment["id"],
                        "literature_id": literature_id,
                        "old_path": str(path),
                        "new_path": str(desired),
                        "changed": str(path) != str(desired),
                    }
                )
        return previews

    def apply_pdf_renames(self, previews: list[dict[str, Any]]) -> int:
        renamed = 0
        for preview in previews:
            old_path = Path(preview["old_path"])
            new_path = Path(preview["new_path"])
            if not preview.get("changed") or not old_path.exists():
                continue
            old_path.rename(new_path)
            stored_path, is_relative = self._store_path(new_path)
            self.connection.execute(
                "UPDATE attachments SET file_path = ?, is_relative = ?, label = ? WHERE id = ?",
                (stored_path, is_relative, new_path.name, preview["attachment_id"]),
            )
            self.connection.execute(
                "INSERT INTO rename_history(attachment_id, old_path, new_path, renamed_at) VALUES(?, ?, ?, ?)",
                (preview["attachment_id"], str(old_path), str(new_path), now_text()),
            )
            renamed += 1
            attachment = self.get_attachment(preview["attachment_id"])
            if attachment:
                self.refresh_search_index_for_literature(int(attachment["literature_id"]))
        self.connection.commit()
        return renamed
