from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeAlias

from ..config import AppSettings
from ..controllers import LibraryController
from ..db import AttachmentRecord, LiteratureRecord, NoteRecord, SearchResult, StatisticsPayload
from ..metadata_fields import metadata_field_label, metadata_fields_for_entry_type, prune_metadata_payload
from ..utils import ENTRY_TYPE_LABELS, READING_STATUSES, ROLE_LABELS, join_csv, note_format_label

FilterValue: TypeAlias = str | int
FilterPayload: TypeAlias = dict[str, FilterValue]
MetadataValue: TypeAlias = str | int | list[str] | None
MetadataPayload: TypeAlias = dict[str, MetadataValue]
ProfileValue: TypeAlias = str | bool | None
ProfileSummary: TypeAlias = dict[str, ProfileValue]


@dataclass(slots=True)
class LiteratureTableRow:
    literature_id: int
    title: str
    year: str
    entry_type: str
    authors: str
    subject: str
    reading_status: str
    attachment_count: int
    note_count: int = 0
    rating: int = 0
    tags: str = ""
    publication_title: str = ""
    publisher: str = ""
    language: str = ""
    doi: str = ""
    cite_key: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class NavigationItem:
    key: str
    label: str
    count: int
    filters: FilterPayload
    helper_text: str = ""
    enabled: bool = True


@dataclass(slots=True)
class StatCard:
    label: str
    value: str
    helper_text: str
    accent: str


class MainWindowViewModel:
    def __init__(self, controller: LibraryController) -> None:
        self.controller = controller

    @property
    def settings(self) -> AppSettings:
        return self.controller.settings

    def clone_controller(self, *, auto_rebuild_index: bool = False) -> LibraryController:
        return self.controller.clone(auto_rebuild_index=auto_rebuild_index)

    def reload_settings_and_database(self) -> AppSettings:
        settings = self.controller.settings_store.load()
        self.controller.settings = settings
        self.controller.reload_database()
        return settings

    def current_library_profile(self) -> ProfileSummary:
        return self.controller.current_library_profile()

    def save_settings(self, settings: AppSettings) -> None:
        self.controller.save_settings(settings)

    def set_ui_theme(self, theme: str) -> str:
        return self.controller.set_ui_theme(theme)

    def list_export_templates(self) -> dict[str, str]:
        return self.controller.list_export_templates()

    def suggested_export_extension(self, template_key: str) -> str:
        return self.controller.suggested_export_extension(template_key)

    def export_bib(self, literature_ids: list[int], destination: str) -> int:
        return self.controller.export_bib(literature_ids, destination)

    def export_csl_json(self, literature_ids: list[int], destination: str) -> int:
        return self.controller.export_csl_json(literature_ids, destination)

    def export_template(self, literature_ids: list[int], template_key: str, destination: str) -> str:
        return self.controller.export_template(literature_ids, template_key, destination)

    def export_statistics(self, template_key: str, destination: str) -> str:
        return self.controller.export_statistics(template_key, destination)

    def build_gbt_references(self, literature_ids: list[int]) -> list[str]:
        return self.controller.build_gbt_references(literature_ids)

    def list_library_profiles(self, *, include_archived: bool = True) -> list[ProfileSummary]:
        return self.controller.list_library_profiles(include_archived=include_archived)

    def create_library_profile(self, name: str) -> ProfileSummary:
        return self.controller.create_library_profile(name)

    def switch_library_profile(self, name: str) -> ProfileSummary:
        return self.controller.switch_library_profile(name)

    def set_library_archived(self, name: str, archived: bool) -> ProfileSummary:
        return self.controller.set_library_archived(name, archived)

    def search_literatures(self, query: str, limit: int = 100) -> list[SearchResult]:
        return self.controller.search_literatures(query, limit=limit)

    def get_statistics(self) -> StatisticsPayload:
        return self.controller.get_statistics()

    def detail_payload(self, literature_id: int) -> LiteratureRecord:
        return self.controller.get_literature(literature_id) or {}

    def apply_metadata_payload(
        self,
        literature_id: int,
        payload: Mapping[str, object],
    ) -> LiteratureRecord | None:
        return self.controller.apply_metadata_payload(literature_id, dict(payload))

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
        return self.controller.save_note(
            literature_id=literature_id,
            title=title,
            content=content,
            attachment_ids=attachment_ids,
            note_id=note_id,
            note_type=note_type,
            note_format=note_format,
            external_file_path=external_file_path,
            import_mode=import_mode,
        )

    def get_note(self, note_id: int) -> NoteRecord | None:
        return self.controller.get_note(note_id)

    def delete_note(self, note_id: int, *, delete_file: bool = False) -> None:
        self.controller.delete_note(note_id, delete_file=delete_file)

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
        return self.controller.add_attachments(
            literature_id,
            files,
            role=role,
            language=language,
            import_mode=import_mode,
            is_primary=is_primary,
        )

    def get_attachment(self, attachment_id: int) -> AttachmentRecord | None:
        return self.controller.get_attachment(attachment_id)

    def delete_attachment(self, attachment_id: int, *, delete_file: bool) -> None:
        self.controller.delete_attachment(attachment_id, delete_file=delete_file)

    def normalize_metadata_payload(self, payload: Mapping[str, object]) -> MetadataPayload:
        normalized: MetadataPayload = {
            "entry_type": self._text_value(payload.get("entry_type")) or "journal_article",
            "title": self._text_value(payload.get("title")) or "未命名文献",
            "subtitle": self._text_value(payload.get("subtitle")),
            "translated_title": self._text_value(payload.get("translated_title")),
            "authors": self._normalize_text_list(payload.get("authors")),
            "translators": self._text_value(payload.get("translators")),
            "editors": self._text_value(payload.get("editors")),
            "year": self._optional_int(payload.get("year")),
            "month": self._text_value(payload.get("month")),
            "day": self._text_value(payload.get("day")),
            "subject": self._text_value(payload.get("subject")),
            "keywords": self._text_value(payload.get("keywords")),
            "tags": self._normalize_text_list(payload.get("tags")),
            "reading_status": self._text_value(payload.get("reading_status")) or READING_STATUSES[0],
            "rating": self._optional_int(payload.get("rating")),
            "publication_title": self._text_value(payload.get("publication_title")),
            "publisher": self._text_value(payload.get("publisher")),
            "publication_place": self._text_value(payload.get("publication_place")),
            "school": self._text_value(payload.get("school")),
            "institution": self._text_value(payload.get("institution")),
            "conference_name": self._text_value(payload.get("conference_name")),
            "conference_place": self._text_value(payload.get("conference_place")),
            "degree": self._text_value(payload.get("degree")),
            "edition": self._text_value(payload.get("edition")),
            "standard_number": self._text_value(payload.get("standard_number")),
            "patent_number": self._text_value(payload.get("patent_number")),
            "report_number": self._text_value(payload.get("report_number")),
            "volume": self._text_value(payload.get("volume")),
            "issue": self._text_value(payload.get("issue")),
            "pages": self._text_value(payload.get("pages")),
            "doi": self._text_value(payload.get("doi")),
            "isbn": self._text_value(payload.get("isbn")),
            "url": self._text_value(payload.get("url")),
            "access_date": self._text_value(payload.get("access_date")),
            "language": self._text_value(payload.get("language")),
            "country": self._text_value(payload.get("country")),
            "summary": self._text_value(payload.get("summary")),
            "abstract": self._text_value(payload.get("abstract")),
            "remarks": self._text_value(payload.get("remarks")),
            "cite_key": self._text_value(payload.get("cite_key")),
        }
        return prune_metadata_payload(normalized, entry_type=normalized.get("entry_type"))

    def save_metadata(self, literature_id: int, payload: Mapping[str, object]) -> LiteratureRecord:
        current = self.detail_payload(literature_id)
        if not current:
            raise ValueError("文献不存在。")
        merged: LiteratureRecord = dict(current)
        merged.update(self.normalize_metadata_payload(payload))
        merged["id"] = literature_id
        self.controller.save_literature(merged)
        return self.detail_payload(literature_id)

    def create_new_literature(self) -> int:
        return self.controller.save_literature(
            {
                "entry_type": "journal_article",
                "title": "未命名文献",
                "authors": [],
                "tags": [],
                "reading_status": READING_STATUSES[0],
            }
        )

    def delete_literature(self, literature_id: int) -> None:
        if not self.controller.get_literature(literature_id):
            raise ValueError("文献不存在。")
        self.controller.delete_literature(literature_id)

    def quick_stats(self) -> list[StatCard]:
        stats = self.get_statistics()
        by_status = {
            item.get("label", ""): int(item.get("count", 0) or 0)
            for item in stats.get("by_status", [])
        }
        return [
            StatCard(
                label="全部文献",
                value=str(stats.get("total_literatures", 0)),
                helper_text="当前文库中的本地文献条目",
                accent="#0f6cbd",
            ),
            StatCard(
                label="在读项目",
                value=str(by_status.get("在读", 0)),
                helper_text="处于阅读中的重点文献",
                accent="#2d8f6f",
            ),
            StatCard(
                label="附件总数",
                value=str(stats.get("total_attachments", 0)),
                helper_text="原文、译文、补充材料与扫描件",
                accent="#c27c2c",
            ),
            StatCard(
                label="笔记总数",
                value=str(stats.get("total_notes", 0)),
                helper_text="正文笔记与外部笔记文件",
                accent="#2c7f8f",
            ),
        ]

    def navigation_sections(self) -> dict[str, list[NavigationItem]]:
        filters = self.controller.list_filter_values()
        recent_threshold = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        favorites_count = len(self.controller.list_literatures(min_rating=4))
        recent_count = len(self.controller.list_literatures(created_after=recent_threshold))

        sections: dict[str, list[NavigationItem]] = {
            "文库": [
                NavigationItem("all", "全部文献", self._count_all(), {}),
                NavigationItem(
                    "recent",
                    "最近新增",
                    recent_count,
                    {"created_after": recent_threshold},
                    "最近 30 天导入或新建的文献",
                ),
                NavigationItem(
                    "reading",
                    "在读",
                    self._count_by_status("在读"),
                    {"reading_status": "在读"},
                    "当前重点阅读队列",
                ),
                NavigationItem(
                    "favorites",
                    "高分条目",
                    favorites_count,
                    {"min_rating": 4},
                    "评分大于等于 4 的文献",
                ),
            ]
        }

        if filters.get("subjects"):
            sections["主题"] = [
                NavigationItem(
                    key=f"subject:{subject}",
                    label=subject,
                    count=len(self.controller.list_literatures(subject=subject)),
                    filters={"subject": subject},
                )
                for subject in filters["subjects"]
            ]

        if filters.get("years"):
            sections["年份"] = [
                NavigationItem(
                    key=f"year:{year}",
                    label=year,
                    count=len(self.controller.list_literatures(year=year)),
                    filters={"year": year},
                )
                for year in filters["years"]
            ]

        if filters.get("tags"):
            sections["标签"] = [
                NavigationItem(
                    key=f"tag:{tag}",
                    label=tag,
                    count=len(self.controller.list_literatures(tag=tag)),
                    filters={"tag": tag},
                )
                for tag in filters["tags"]
            ]

        return sections

    def list_rows(
        self,
        *,
        search: str = "",
        subject: str = "",
        year: str = "",
        entry_type: str = "",
        filters: FilterPayload | None = None,
    ) -> list[LiteratureTableRow]:
        active_filters = dict(filters or {})
        rows = self.controller.list_literatures(
            search=search,
            subject=subject or str(active_filters.get("subject", "")),
            year=year or str(active_filters.get("year", "")),
            entry_type=entry_type or str(active_filters.get("entry_type", "")),
            tag=str(active_filters.get("tag", "")),
            reading_status=str(active_filters.get("reading_status", "")),
            min_rating=int(active_filters.get("min_rating", 0) or 0),
            created_after=str(active_filters.get("created_after", "")),
        )
        return [
            LiteratureTableRow(
                literature_id=int(row["id"]),
                title=str(row.get("title", "")),
                year=str(row.get("year") or ""),
                entry_type=ENTRY_TYPE_LABELS.get(str(row.get("entry_type", "")), str(row.get("entry_type", ""))),
                authors=str(row.get("authors_display", "")),
                subject=str(row.get("subject", "")),
                reading_status=str(row.get("reading_status", "")),
                attachment_count=int(row.get("attachment_count", 0)),
                note_count=int(row.get("note_count", 0)),
                rating=int(row.get("rating") or 0),
                tags=str(row.get("tags_display", "")),
                publication_title=str(row.get("publication_title", "")),
                publisher=str(row.get("publisher", "")),
                language=str(row.get("language", "")),
                doi=str(row.get("doi", "")),
                cite_key=str(row.get("cite_key", "")),
                created_at=self._format_table_timestamp(row.get("created_at")),
                updated_at=self._format_table_timestamp(row.get("updated_at")),
            )
            for row in rows
        ]

    @staticmethod
    def _format_table_timestamp(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return text
        return parsed.strftime("%Y-%m-%d %H:%M")

    def filter_summary(self, filters: FilterPayload | None = None) -> str:
        active_filters = dict(filters or {})
        if not active_filters:
            return "全部文献"
        parts: list[str] = []
        if active_filters.get("subject"):
            parts.append(f"主题：{active_filters['subject']}")
        if active_filters.get("year"):
            parts.append(f"年份：{active_filters['year']}")
        if active_filters.get("tag"):
            parts.append(f"标签：{active_filters['tag']}")
        if active_filters.get("reading_status"):
            parts.append(f"状态：{active_filters['reading_status']}")
        if active_filters.get("min_rating"):
            parts.append(f"评分 >= {active_filters['min_rating']}")
        if active_filters.get("created_after"):
            parts.append("最近新增")
        return " | ".join(parts)

    def metadata_lines(self, literature_id: int) -> list[str]:
        detail = self.detail_payload(literature_id)
        if not detail:
            return []
        entry_type = detail.get("entry_type")
        lines = [f"类型：{ENTRY_TYPE_LABELS.get(str(entry_type or ''), str(entry_type or ''))}"]
        for field in metadata_fields_for_entry_type(entry_type):
            if field in {"entry_type", "summary", "abstract", "remarks", "reading_status", "rating"}:
                continue
            if field == "authors":
                value = " / ".join(detail.get("authors", []))
            elif field == "tags":
                value = join_csv(detail.get("tags", []))
            else:
                value = detail.get(field, "")
            text = self._text_value(value)
            if text:
                lines.append(f"{metadata_field_label(field, entry_type)}：{text}")
        if detail.get("reading_status"):
            lines.append(f"阅读状态：{detail.get('reading_status')}")
        if detail.get("rating"):
            lines.append(f"评分：{detail.get('rating')}")
        lines.extend(
            [
                "",
                "简介：",
                detail.get("summary") or "",
                "",
                "摘要：",
                detail.get("abstract") or "",
                "",
                "备注：",
                detail.get("remarks") or "",
            ]
        )
        return lines

    def attachment_lines(self, literature_id: int) -> list[str]:
        detail = self.detail_payload(literature_id)
        lines: list[str] = []
        for attachment in detail.get("attachments", []):
            lines.append(
                " | ".join(
                    part
                    for part in [
                        attachment.get("label", ""),
                        ROLE_LABELS.get(attachment.get("role", ""), attachment.get("role", "")),
                        attachment.get("language", ""),
                        attachment.get("resolved_path", ""),
                    ]
                    if part
                )
            )
        return lines

    def note_lines(self, literature_id: int) -> list[str]:
        detail = self.detail_payload(literature_id)
        lines: list[str] = []
        for note in detail.get("notes", []):
            descriptor = note.get("title", "")
            if note.get("note_type") == "file":
                descriptor += f" ({note_format_label(note.get('note_format', 'text'))})"
            lines.append(descriptor)
        return lines

    @staticmethod
    def _text_value(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @classmethod
    def _normalize_text_list(cls, value: object) -> list[str]:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, (list, tuple, set)):
            candidates = [str(item) for item in value]
        else:
            candidates = []
        normalized: list[str] = []
        for item in candidates:
            text = cls._text_value(item)
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value or None
        if isinstance(value, float):
            number = int(value)
            return number or None
        if isinstance(value, str) and value.strip().isdigit():
            number = int(value.strip())
            return number or None
        return None

    def _count_all(self) -> int:
        return int(self.get_statistics().get("total_literatures", 0))

    def _count_by_status(self, status: str) -> int:
        return len(self.controller.list_literatures(reading_status=status))
