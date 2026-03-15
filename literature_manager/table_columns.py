from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiteratureColumnSpec:
    key: str
    label: str
    default_width: int
    alignment: str = "left"


AVAILABLE_LITERATURE_COLUMNS: tuple[LiteratureColumnSpec, ...] = (
    LiteratureColumnSpec("title", "标题", 360),
    LiteratureColumnSpec("year", "年份", 84, "center"),
    LiteratureColumnSpec("entry_type", "类型", 110),
    LiteratureColumnSpec("authors", "作者", 220),
    LiteratureColumnSpec("subject", "主题", 160),
    LiteratureColumnSpec("reading_status", "阅读状态", 100, "center"),
    LiteratureColumnSpec("attachment_count", "附件数", 84, "center"),
    LiteratureColumnSpec("note_count", "笔记数", 84, "center"),
    LiteratureColumnSpec("rating", "评分", 72, "center"),
    LiteratureColumnSpec("tags", "标签", 180),
    LiteratureColumnSpec("publication_title", "出版源", 220),
    LiteratureColumnSpec("publisher", "出版社", 180),
    LiteratureColumnSpec("language", "语言", 100),
    LiteratureColumnSpec("doi", "DOI", 220),
    LiteratureColumnSpec("cite_key", "引用键", 180),
    LiteratureColumnSpec("created_at", "创建时间", 160),
    LiteratureColumnSpec("updated_at", "更新时间", 160),
)

DEFAULT_LITERATURE_COLUMN_KEYS: tuple[str, ...] = (
    "title",
    "year",
    "entry_type",
    "authors",
    "subject",
    "reading_status",
    "attachment_count",
)

_COLUMN_BY_KEY = {item.key: item for item in AVAILABLE_LITERATURE_COLUMNS}


def available_literature_columns() -> list[LiteratureColumnSpec]:
    return list(AVAILABLE_LITERATURE_COLUMNS)


def literature_column_by_key(key: str) -> LiteratureColumnSpec | None:
    return _COLUMN_BY_KEY.get(key)


def normalize_literature_column_keys(keys: list[str] | tuple[str, ...] | None) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_key in keys or []:
        key = str(raw_key).strip()
        if key not in _COLUMN_BY_KEY or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    if ordered:
        return ordered
    return list(DEFAULT_LITERATURE_COLUMN_KEYS)


def normalize_literature_column_widths(widths: dict[str, object] | None) -> dict[str, int]:
    if not isinstance(widths, dict):
        return {}
    normalized: dict[str, int] = {}
    for raw_key, raw_width in widths.items():
        key = str(raw_key).strip()
        if key not in _COLUMN_BY_KEY:
            continue
        try:
            width = int(raw_width)
        except (TypeError, ValueError):
            continue
        if width < 40:
            continue
        normalized[key] = width
    return normalized
