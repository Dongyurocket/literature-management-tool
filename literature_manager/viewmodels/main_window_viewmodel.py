from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..controllers import LibraryController
from ..utils import ENTRY_TYPE_LABELS, ROLE_LABELS, join_csv, note_format_label


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


@dataclass(slots=True)
class NavigationItem:
    key: str
    label: str
    count: int
    filters: dict[str, Any]
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

    def quick_stats(self) -> list[StatCard]:
        stats = self.controller.get_statistics()
        by_status = {
            item.get("label", ""): int(item.get("count", 0) or 0)
            for item in stats.get("by_status", [])
        }
        return [
            StatCard(
                label="All Literature",
                value=str(stats.get("total_literatures", 0)),
                helper_text="Local-first reference records",
                accent="#0f6cbd",
            ),
            StatCard(
                label="Reading Now",
                value=str(by_status.get("在读", 0)),
                helper_text="Items currently in progress",
                accent="#2d8f6f",
            ),
            StatCard(
                label="Attachments",
                value=str(stats.get("total_attachments", 0)),
                helper_text="PDF, translation, note files",
                accent="#c27c2c",
            ),
            StatCard(
                label="Notes",
                value=str(stats.get("total_notes", 0)),
                helper_text="Inline and linked note assets",
                accent="#2c7f8f",
            ),
        ]

    def navigation_sections(self) -> dict[str, list[NavigationItem]]:
        filters = self.controller.list_filter_values()
        recent_threshold = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        favorites_count = len(self.controller.list_literatures(min_rating=4))
        recent_count = len(self.controller.list_literatures(created_after=recent_threshold))

        sections: dict[str, list[NavigationItem]] = {
            "Library": [
                NavigationItem("all", "All Literature", self._count_all(), {}),
                NavigationItem(
                    "recent",
                    "Recently Added",
                    recent_count,
                    {"created_after": recent_threshold},
                    "Added in the last 30 days",
                ),
                NavigationItem(
                    "reading",
                    "Reading Now",
                    self._count_by_status("在读"),
                    {"reading_status": "在读"},
                    "Focused reading queue",
                ),
                NavigationItem(
                    "favorites",
                    "Favorites",
                    favorites_count,
                    {"min_rating": 4},
                    "Rating 4 or above",
                ),
                NavigationItem(
                    "recycle-bin",
                    "Recycle Bin",
                    0,
                    {},
                    "Reserved for a future soft-delete flow",
                    enabled=False,
                ),
            ]
        }

        if filters.get("subjects"):
            sections["Subjects"] = [
                NavigationItem(
                    key=f"subject:{subject}",
                    label=subject,
                    count=len(self.controller.list_literatures(subject=subject)),
                    filters={"subject": subject},
                )
                for subject in filters["subjects"]
            ]

        if filters.get("years"):
            sections["Years"] = [
                NavigationItem(
                    key=f"year:{year}",
                    label=year,
                    count=len(self.controller.list_literatures(year=year)),
                    filters={"year": year},
                )
                for year in filters["years"]
            ]

        if filters.get("tags"):
            sections["Tags"] = [
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
        filters: dict[str, Any] | None = None,
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
                title=row.get("title", ""),
                year=str(row.get("year") or ""),
                entry_type=ENTRY_TYPE_LABELS.get(row.get("entry_type", ""), row.get("entry_type", "")),
                authors=row.get("authors_display", ""),
                subject=row.get("subject", ""),
                reading_status=row.get("reading_status", ""),
                attachment_count=int(row.get("attachment_count", 0)),
            )
            for row in rows
        ]

    def filter_summary(self, filters: dict[str, Any] | None = None) -> str:
        active_filters = dict(filters or {})
        if not active_filters:
            return "All literature"
        parts: list[str] = []
        if active_filters.get("subject"):
            parts.append(f"Subject: {active_filters['subject']}")
        if active_filters.get("year"):
            parts.append(f"Year: {active_filters['year']}")
        if active_filters.get("tag"):
            parts.append(f"Tag: {active_filters['tag']}")
        if active_filters.get("reading_status"):
            parts.append(f"Status: {active_filters['reading_status']}")
        if active_filters.get("min_rating"):
            parts.append(f"Rating >= {active_filters['min_rating']}")
        if active_filters.get("created_after"):
            parts.append("Recently added")
        return " | ".join(parts)

    def detail_payload(self, literature_id: int) -> dict[str, Any]:
        return self.controller.get_literature(literature_id) or {}

    def metadata_lines(self, literature_id: int) -> list[str]:
        detail = self.detail_payload(literature_id)
        if not detail:
            return []
        lines = [
            f"Title: {detail.get('title') or ''}",
            f"Translated: {detail.get('translated_title') or ''}",
            f"Authors: {' / '.join(detail.get('authors', []))}",
            f"Year: {detail.get('year') or ''}",
            f"Subject: {detail.get('subject') or ''}",
            f"Keywords: {detail.get('keywords') or ''}",
            f"Publication: {detail.get('publication_title') or ''}",
            f"Publisher/School: {detail.get('publisher') or detail.get('school') or ''}",
            f"DOI: {detail.get('doi') or ''}",
            f"ISBN: {detail.get('isbn') or ''}",
            f"Language: {detail.get('language') or ''}",
            f"Country: {detail.get('country') or ''}",
            f"Reading Status: {detail.get('reading_status') or ''}",
            f"Tags: {join_csv(detail.get('tags', []))}",
            "",
            "Summary:",
            detail.get("summary") or "",
            "",
            "Abstract:",
            detail.get("abstract") or "",
            "",
            "Remarks:",
            detail.get("remarks") or "",
        ]
        return lines

    def attachment_lines(self, literature_id: int) -> list[str]:
        detail = self.detail_payload(literature_id)
        lines = []
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
        lines = []
        for note in detail.get("notes", []):
            descriptor = note.get("title", "")
            if note.get("note_type") == "file":
                descriptor += f" ({note_format_label(note.get('note_format', 'text'))})"
            lines.append(descriptor)
        return lines

    def _count_all(self) -> int:
        return int(self.controller.get_statistics().get("total_literatures", 0))

    def _count_by_status(self, status: str) -> int:
        return len(self.controller.list_literatures(reading_status=status))
