from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COMMON_METADATA_FIELDS = (
    "entry_type",
    "title",
    "subtitle",
    "translated_title",
    "authors",
    "translators",
    "editors",
    "year",
    "month",
    "day",
    "doi",
    "isbn",
    "url",
    "language",
    "country",
    "subject",
    "keywords",
    "tags",
    "reading_status",
    "rating",
    "summary",
    "abstract",
    "remarks",
    "cite_key",
)

TYPE_METADATA_FIELDS = {
    "journal_article": (
        "publication_title",
        "volume",
        "issue",
        "pages",
    ),
    "book": (
        "publisher",
        "publication_place",
        "edition",
    ),
    "thesis": (
        "school",
        "degree",
        "publication_place",
    ),
    "conference_paper": (
        "publication_title",
        "conference_name",
        "conference_place",
        "publisher",
        "pages",
    ),
    "standard": (
        "standard_number",
        "publisher",
        "publication_place",
    ),
    "patent": (
        "patent_number",
        "institution",
    ),
    "report": (
        "institution",
        "report_number",
        "publisher",
        "publication_place",
    ),
    "webpage": (
        "publication_title",
        "publisher",
        "institution",
        "access_date",
    ),
    "misc": (
        "publication_title",
        "publisher",
        "institution",
        "publication_place",
        "pages",
    ),
}

FIELD_LABELS = {
    "entry_type": "类型",
    "title": "标题",
    "subtitle": "副标题",
    "translated_title": "译题",
    "authors": "作者",
    "translators": "译者",
    "editors": "编者",
    "year": "年份",
    "month": "月",
    "day": "日",
    "publication_title": "载体标题",
    "publisher": "出版社",
    "publication_place": "出版地",
    "school": "学校",
    "institution": "机构",
    "conference_name": "会议名",
    "conference_place": "会议地点",
    "degree": "学位类别",
    "edition": "版次",
    "standard_number": "标准号",
    "patent_number": "专利号",
    "report_number": "报告号",
    "volume": "卷",
    "issue": "期",
    "pages": "页码",
    "doi": "DOI",
    "isbn": "ISBN",
    "url": "URL",
    "access_date": "访问日期",
    "language": "语言",
    "country": "国家 / 地区",
    "subject": "主题",
    "keywords": "关键词",
    "tags": "标签",
    "reading_status": "阅读状态",
    "rating": "评分",
    "summary": "简介",
    "abstract": "摘要",
    "remarks": "备注",
    "cite_key": "引用键",
}

ENTRY_TYPE_FIELD_LABEL_OVERRIDES = {
    "journal_article": {
        "publication_title": "期刊名",
    },
    "conference_paper": {
        "publication_title": "论文集 / 会议录",
    },
    "thesis": {
        "school": "学位授予单位",
        "publication_place": "授予地",
    },
    "report": {
        "institution": "报告机构",
        "publisher": "发布单位",
    },
    "standard": {
        "publisher": "发布单位",
    },
    "patent": {
        "institution": "专利权人 / 机构",
    },
    "webpage": {
        "publication_title": "网站 / 栏目",
        "publisher": "发布者",
        "institution": "网站机构",
    },
}

ALL_TYPE_SPECIFIC_FIELDS = tuple(
    dict.fromkeys(field for fields in TYPE_METADATA_FIELDS.values() for field in fields)
)
ALL_METADATA_FIELDS = tuple(dict.fromkeys((*COMMON_METADATA_FIELDS, *ALL_TYPE_SPECIFIC_FIELDS)))


def normalize_entry_type(entry_type: str | None) -> str:
    normalized = str(entry_type or "").strip().lower()
    if normalized in TYPE_METADATA_FIELDS:
        return normalized
    return "misc"


def metadata_fields_for_entry_type(entry_type: str | None) -> tuple[str, ...]:
    normalized = normalize_entry_type(entry_type)
    return tuple(dict.fromkeys((*COMMON_METADATA_FIELDS, *TYPE_METADATA_FIELDS[normalized])))


def metadata_field_set(entry_type: str | None) -> set[str]:
    return set(metadata_fields_for_entry_type(entry_type))


def metadata_field_label(field: str, entry_type: str | None = None) -> str:
    normalized = normalize_entry_type(entry_type)
    overrides = ENTRY_TYPE_FIELD_LABEL_OVERRIDES.get(normalized, {})
    return overrides.get(field, FIELD_LABELS.get(field, field))


def empty_metadata_value(value: Any) -> Any:
    if isinstance(value, list):
        return []
    if isinstance(value, tuple):
        return []
    if isinstance(value, set):
        return []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    return ""


def prune_metadata_payload(
    payload: Mapping[str, Any],
    *,
    entry_type: str | None = None,
) -> dict[str, Any]:
    normalized_type = normalize_entry_type(entry_type or payload.get("entry_type"))
    allowed_fields = metadata_field_set(normalized_type)
    pruned = dict(payload)
    pruned["entry_type"] = normalized_type
    for field in ALL_TYPE_SPECIFIC_FIELDS:
        if field in allowed_fields:
            continue
        if field in pruned:
            pruned[field] = empty_metadata_value(pruned[field])
    return pruned
