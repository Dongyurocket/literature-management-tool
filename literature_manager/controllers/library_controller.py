from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import AppSettings, SettingsStore
from ..db import LibraryDatabase
from ..dedupe_service import find_duplicate_groups, merge_literatures
from ..import_service import import_scanned_items, scan_import_sources
from ..maintenance_service import create_backup, find_missing_paths, repair_missing_paths, restore_backup
from ..metadata_service import lookup_doi, lookup_isbn
from ..utils import build_csl_entry, build_gbt_reference


class LibraryController:
    def __init__(
        self,
        settings_store: SettingsStore,
        settings: AppSettings,
        *,
        auto_rebuild_index: bool = True,
    ) -> None:
        self.settings_store = settings_store
        self.settings = settings
        self.database = self._open_database()
        if auto_rebuild_index:
            self.database.rebuild_search_index()

    def _open_database(self) -> LibraryDatabase:
        return LibraryDatabase(
            self.settings_store.database_path,
            lambda: self.settings.library_root,
        )

    def close(self) -> None:
        self.database.close()

    def reload_database(self) -> None:
        self.database.close()
        self.database = self._open_database()
        self.database.rebuild_search_index()

    def save_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self.settings_store.save(self.settings)

    def clone(self, *, auto_rebuild_index: bool = False) -> "LibraryController":
        return LibraryController(
            self.settings_store,
            AppSettings(**asdict(self.settings)),
            auto_rebuild_index=auto_rebuild_index,
        )

    def set_ui_theme(self, theme: str) -> str:
        normalized = theme if theme in {"system", "light", "dark"} else "system"
        self.settings.ui_theme = normalized
        self.settings_store.save(self.settings)
        return normalized

    def list_filter_values(self) -> dict[str, list[str]]:
        return self.database.list_filter_values()

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
        return self.database.list_literatures(
            search=search,
            subject=subject,
            year=year,
            entry_type=entry_type,
            tag=tag,
            reading_status=reading_status,
            min_rating=min_rating,
            created_after=created_after,
        )

    def get_literature(self, literature_id: int) -> dict[str, Any] | None:
        return self.database.get_literature(literature_id)

    def save_literature(self, payload: dict[str, Any]) -> int:
        return self.database.save_literature(payload)

    def delete_literature(self, literature_id: int) -> None:
        self.database.delete_literature(literature_id)

    def get_attachment(self, attachment_id: int) -> dict[str, Any] | None:
        return self.database.get_attachment(attachment_id)

    def add_attachments(
        self,
        literature_id: int,
        files: list[str],
        **kwargs: Any,
    ) -> list[int]:
        return self.database.add_attachments(literature_id, files, **kwargs)

    def delete_attachment(self, attachment_id: int, *, delete_file: bool) -> None:
        self.database.delete_attachment(attachment_id, delete_file=delete_file)

    def get_note(self, note_id: int) -> dict[str, Any] | None:
        return self.database.get_note(note_id)

    def save_note(self, **kwargs: Any) -> int:
        return self.database.save_note(**kwargs)

    def delete_note(self, note_id: int, *, delete_file: bool = False) -> None:
        self.database.delete_note(note_id, delete_file=delete_file)

    def search_literatures(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.database.search_literatures(query, limit=limit)

    def get_statistics(self) -> dict[str, Any]:
        return self.database.get_statistics()

    def rebuild_search_index(self) -> None:
        self.database.rebuild_search_index()

    def import_items(self, items: list[dict[str, Any]], *, import_mode: str | None = None) -> dict[str, int]:
        return import_scanned_items(
            self.database,
            items,
            self.settings,
            import_mode=import_mode,
        )

    def scan_import_sources(self, paths: list[str], *, recursive: bool = True) -> list[dict[str, Any]]:
        return scan_import_sources(paths, recursive=recursive)

    def import_paths(
        self,
        paths: list[str],
        *,
        recursive: bool = True,
        import_mode: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        items = self.scan_import_sources(paths, recursive=recursive)
        result = self.import_items(items, import_mode=import_mode)
        return items, result

    def lookup_metadata_for_literature(
        self,
        literature_id: int,
        manual_identifier: str = "",
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        detail = self.get_literature(literature_id)
        if not detail:
            return None, None
        identifier = detail.get("doi") or detail.get("isbn") or manual_identifier.strip()
        if not identifier:
            raise ValueError("请输入 DOI 或 ISBN。")
        if str(identifier).lower().startswith("10."):
            return detail, lookup_doi(str(identifier))
        return detail, lookup_isbn(str(identifier))

    def merge_metadata_payload(self, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        payload = dict(current)
        for key, value in incoming.items():
            if key in {"id", "attachments", "notes"}:
                continue
            if key == "authors":
                if not payload.get("authors"):
                    payload["authors"] = value
            elif key == "tags":
                existing = list(payload.get("tags", []))
                for tag in value or []:
                    if tag not in existing:
                        existing.append(tag)
                payload["tags"] = existing
            elif value and not payload.get(key):
                payload[key] = value
        return payload

    def apply_metadata_payload(self, literature_id: int, incoming: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_literature(literature_id)
        if not current:
            return None
        merged = self.merge_metadata_payload(current, incoming)
        self.save_literature(merged)
        return self.get_literature(literature_id)

    def find_duplicate_groups(self) -> list[dict[str, Any]]:
        return find_duplicate_groups(self.database)

    def merge_duplicates(self, primary_id: int, merged_ids: list[int], reason: str) -> None:
        merge_literatures(self.database, primary_id, merged_ids, reason)

    def find_missing_paths(self) -> list[dict[str, Any]]:
        return find_missing_paths(self.database)

    def repair_missing_paths(self, folder: str) -> dict[str, int]:
        return repair_missing_paths(self.database, folder)

    def create_backup(self, destination: str, *, include_library: bool = True) -> str:
        return create_backup(
            self.settings_store,
            self.settings,
            destination,
            include_library=include_library,
        )

    def restore_backup(self, backup_path: str) -> AppSettings:
        self.settings = restore_backup(self.settings_store, backup_path)
        self.reload_database()
        return self.settings

    def export_bib(self, literature_ids: list[int], destination: str) -> int:
        count = self.database.export_bib(literature_ids, destination)
        self.settings.recent_export_dir = str(Path(destination).expanduser().resolve().parent)
        self.settings_store.save(self.settings)
        return count

    def export_csl_json(self, literature_ids: list[int], destination: str) -> int:
        entries = []
        for literature_id in literature_ids:
            detail = self.get_literature(literature_id)
            if detail:
                entries.append(build_csl_entry(detail))
        Path(destination).write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.settings.recent_export_dir = str(Path(destination).expanduser().resolve().parent)
        self.settings_store.save(self.settings)
        return len(entries)

    def build_gbt_references(self, literature_ids: list[int]) -> list[str]:
        references: list[str] = []
        for literature_id in literature_ids:
            detail = self.get_literature(literature_id)
            if detail:
                references.append(build_gbt_reference(detail))
        return [item for item in references if item]

    def preview_pdf_renames(self, literature_ids: list[int]) -> list[dict[str, Any]]:
        return self.database.preview_pdf_renames(literature_ids)

    def apply_pdf_renames(self, previews: list[dict[str, Any]]) -> int:
        return self.database.apply_pdf_renames(previews)
