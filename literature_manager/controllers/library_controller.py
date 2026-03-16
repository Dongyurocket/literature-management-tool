from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import AppSettings, SettingsStore
from ..db import LibraryDatabase
from ..dedupe_service import find_duplicate_groups, merge_literatures
from ..export_service import (
    export_statistics_report,
    export_template_file,
    list_export_templates,
    list_report_templates,
    suggested_extension,
)
from ..import_service import import_scanned_items, scan_import_sources
from ..maintenance_service import create_backup, find_missing_paths, repair_missing_paths, restore_backup
from ..metadata_fields import normalize_entry_type, prune_metadata_payload
from ..metadata_service import lookup_doi, lookup_isbn, lookup_title_metadata
from ..ocr_service import install_umi_ocr
from ..update_service import check_latest_release, download_release_asset
from ..utils import build_csl_entry, build_gbt_reference


class LibraryController:
    _KNOWN_METADATA_SOURCES = {
        "crossref",
        "datacite",
        "openalex",
        "cnki",
        "ustc_openurl",
        "tsinghua_openurl",
        "openlibrary",
        "googlebooks",
    }
    _TITLE_PLACEHOLDERS = {"未命名文献", "untitled"}
    _AUTHOR_PLACEHOLDERS = {"佚名", "匿名", "unknown", "unknown author", "n/a"}

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
            lambda: self.settings,
        )

    def _preferred_metadata_sources(self) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in self.settings.metadata_sources or []:
            source = str(item).strip()
            if source not in self._KNOWN_METADATA_SOURCES or source in seen:
                continue
            seen.add(source)
            normalized.append(source)
        return normalized

    @classmethod
    def _is_effectively_empty(cls, key: str, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return True
            lowered = text.lower()
            if key == "title" and lowered in {item.lower() for item in cls._TITLE_PLACEHOLDERS}:
                return True
            if key in {"subject", "reading_status"} and text in {"未分类", "未标注"}:
                return True
            return False
        if isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if not items:
                return True
            if key == "authors":
                return all(item.lower() in cls._AUTHOR_PLACEHOLDERS for item in items)
            return False
        return False

    @staticmethod
    def _split_keywords(value: str) -> list[str]:
        parts = re.split(r"[;,；，、]+", value)
        return [part.strip() for part in parts if part.strip()]

    @classmethod
    def _merge_keywords(cls, current: str, incoming: str) -> str:
        current_items = cls._split_keywords(current)
        incoming_items = cls._split_keywords(incoming)
        merged = list(current_items)
        for item in incoming_items:
            if item not in merged:
                merged.append(item)
        return "；".join(merged)

    @staticmethod
    def _normalize_statistics_labels(stats: dict[str, Any]) -> dict[str, Any]:
        payload = dict(stats)

        def normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized: list[dict[str, Any]] = []
            for item in items:
                row = dict(item)
                label = str(row.get("label", "") or "")
                if any(token in label for token in ["鏈", "娉", "绫"]):
                    if row in stats.get("by_year", []):
                        row["label"] = "未标注"
                    elif row in stats.get("by_subject", []):
                        row["label"] = "未分类"
                    else:
                        row["label"] = "未标注"
                normalized.append(row)
            return normalized

        payload["by_year"] = normalize_items(list(stats.get("by_year", [])))
        payload["by_subject"] = normalize_items(list(stats.get("by_subject", [])))
        payload["by_status"] = normalize_items(list(stats.get("by_status", [])))
        return payload

    @staticmethod
    def _is_doi(identifier: str) -> bool:
        text = identifier.strip().lower()
        return text.startswith("10.") or "doi.org/" in text

    @staticmethod
    def _is_isbn(identifier: str) -> bool:
        normalized = re.sub(r"[^0-9Xx]", "", identifier or "")
        return len(normalized) in {10, 13}

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

    def current_library_profile(self) -> dict[str, Any]:
        profile = self.settings_store.current_profile()
        summary = next(
            (item for item in self.settings_store.profile_summary() if item["name"] == profile.name),
            None,
        )
        return summary or {
            "name": profile.name,
            "slug": profile.slug,
            "archived": profile.archived,
            "active": True,
            "database_path": str(self.settings_store.database_path),
            "settings_path": str(self.settings_store.settings_path),
            "library_root": self.settings.library_root,
        }

    def list_library_profiles(self, *, include_archived: bool = True) -> list[dict[str, Any]]:
        return self.settings_store.profile_summary(include_archived=include_archived)

    def create_library_profile(self, name: str) -> dict[str, Any]:
        profile = self.settings_store.create_profile(name, template_settings=self.settings)
        return next(
            item for item in self.settings_store.profile_summary() if item["name"] == profile.name
        )

    def switch_library_profile(self, name: str) -> dict[str, Any]:
        self.settings_store.switch_profile(name)
        self.settings = self.settings_store.load()
        self.reload_database()
        return self.current_library_profile()

    def set_library_archived(self, name: str, archived: bool) -> dict[str, Any]:
        self.settings_store.set_profile_archived(name, archived)
        self.settings = self.settings_store.load()
        self.reload_database()
        return next(
            item for item in self.settings_store.profile_summary() if item["name"] == name
        )

    def delete_library_profile(self, name: str, *, delete_files: bool = False) -> None:
        self.settings_store.delete_profile(name, delete_files=delete_files)

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

    def reextract_attachment_texts(self, attachment_ids: list[int]) -> dict[str, int]:
        updated = 0
        skipped = 0
        for attachment_id in attachment_ids:
            attachment = self.get_attachment(attachment_id)
            if not attachment:
                skipped += 1
                continue
            path = str(attachment.get("resolved_path", ""))
            if not path.lower().endswith(".pdf"):
                skipped += 1
                continue
            self.database.refresh_attachment_text(attachment_id)
            updated += 1
        return {"updated": updated, "skipped": skipped}

    def get_note(self, note_id: int) -> dict[str, Any] | None:
        return self.database.get_note(note_id)

    def save_note(self, **kwargs: Any) -> int:
        return self.database.save_note(**kwargs)

    def delete_note(self, note_id: int, *, delete_file: bool = False) -> None:
        self.database.delete_note(note_id, delete_file=delete_file)

    def search_literatures(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.database.search_literatures(query, limit=limit)

    def get_statistics(self) -> dict[str, Any]:
        return self._normalize_statistics_labels(self.database.get_statistics())

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
        return scan_import_sources(paths, recursive=recursive, settings=self.settings)

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

        preferred_sources = self._preferred_metadata_sources()
        identifier = (manual_identifier or detail.get("doi") or detail.get("isbn") or "").strip()
        errors: list[str] = []

        if identifier:
            try:
                if self._is_doi(identifier):
                    return detail, lookup_doi(identifier, preferred_sources=preferred_sources)
                if self._is_isbn(identifier):
                    return detail, lookup_isbn(identifier, preferred_sources=preferred_sources)
                raise ValueError("请输入合法的 DOI 或 ISBN。")
            except ValueError as exc:
                errors.append(str(exc))

        try:
            payload = lookup_title_metadata(
                detail.get("title", ""),
                authors=detail.get("authors", []),
                year=detail.get("year"),
                entry_type=detail.get("entry_type"),
                preferred_sources=preferred_sources,
            )
            if errors:
                payload["metadata_lookup_notice"] = "标识符查询失败，已回退到标题检索。"
                payload["metadata_lookup_errors"] = errors
            return detail, payload
        except ValueError as exc:
            errors.append(str(exc))

        raise ValueError("；".join(errors) or "无法获取文献元数据。")

    def merge_metadata_payload(self, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        payload = dict(current)
        current_entry_type = normalize_entry_type(payload.get("entry_type"))
        for key, value in incoming.items():
            if key in {"id", "attachments", "notes"}:
                continue
            if key == "source_provider" or str(key).startswith("metadata_"):
                continue
            if key == "entry_type":
                incoming_entry_type = normalize_entry_type(value)
                if current_entry_type == "misc" and incoming_entry_type != "misc":
                    payload["entry_type"] = incoming_entry_type
                    current_entry_type = incoming_entry_type
                continue
            if key == "authors":
                incoming_authors = [str(item).strip() for item in (value or []) if str(item).strip()]
                if not incoming_authors:
                    continue
                existing_authors = [str(item).strip() for item in (payload.get("authors") or []) if str(item).strip()]
                if self._is_effectively_empty("authors", existing_authors):
                    payload["authors"] = incoming_authors
                    continue
                merged_authors = list(existing_authors)
                for author in incoming_authors:
                    if author not in merged_authors:
                        merged_authors.append(author)
                payload["authors"] = merged_authors
            elif key == "tags":
                existing = list(payload.get("tags", []))
                for tag in value or []:
                    if tag not in existing:
                        existing.append(tag)
                payload["tags"] = existing
            elif key == "keywords":
                incoming_keywords = str(value or "").strip()
                if not incoming_keywords:
                    continue
                current_keywords = str(payload.get("keywords", "") or "").strip()
                if not current_keywords:
                    payload["keywords"] = incoming_keywords
                else:
                    payload["keywords"] = self._merge_keywords(current_keywords, incoming_keywords)
            elif not self._is_effectively_empty(key, value):
                if self._is_effectively_empty(key, payload.get(key)):
                    payload[key] = value
        return prune_metadata_payload(payload, entry_type=payload.get("entry_type"))

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

    def list_export_templates(self) -> dict[str, str]:
        return list_export_templates()

    def list_report_templates(self) -> dict[str, str]:
        return list_report_templates()

    def suggested_export_extension(self, template_key: str) -> str:
        return suggested_extension(template_key)

    def export_template(self, literature_ids: list[int], template_key: str, destination: str) -> str:
        literatures = [self.get_literature(item_id) for item_id in literature_ids]
        payload = [item for item in literatures if item]
        path = export_template_file(
            template_key,
            payload,
            destination,
            library_name=self.current_library_profile().get("name", ""),
        )
        self.settings.recent_export_dir = str(Path(path).parent)
        self.settings_store.save(self.settings)
        return path

    def export_statistics(self, template_key: str, destination: str) -> str:
        path = export_statistics_report(
            template_key,
            self.get_statistics(),
            destination,
            library_name=self.current_library_profile().get("name", ""),
        )
        self.settings.recent_export_dir = str(Path(path).parent)
        self.settings_store.save(self.settings)
        return path

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

    def install_umi_ocr(self) -> dict[str, Any]:
        result = install_umi_ocr(self.settings)
        self.settings_store.save(self.settings)
        return result

    def check_for_updates(self) -> dict[str, Any]:
        return check_latest_release(self.settings.update_repo, __version__)

    def download_update(self, release_info: dict[str, Any], destination_dir: str) -> str:
        asset_url = str(release_info.get("asset_url", "")).strip()
        asset_name = str(release_info.get("asset_name", "")).strip() or "LiteratureManagementTool-Setup.exe"
        if not asset_url:
            raise ValueError("当前发布没有可下载的安装包。")
        target = Path(destination_dir).expanduser().resolve() / asset_name
        return download_release_asset(asset_url, target)
