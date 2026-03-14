from __future__ import annotations

from typing import Any

from .db import LibraryDatabase
from .metadata_service import normalized_title_key


def find_duplicate_groups(database: LibraryDatabase) -> list[dict[str, Any]]:
    literatures = database.list_literatures()
    by_doi: dict[str, list[dict[str, Any]]] = {}
    by_title_year: dict[str, list[dict[str, Any]]] = {}

    for literature in literatures:
        detail = database.get_literature(int(literature["id"]))
        if not detail:
            continue
        doi = (detail.get("doi") or "").strip().lower()
        if doi:
            by_doi.setdefault(doi, []).append(detail)
        title_key = normalized_title_key(detail.get("title", ""))
        year = detail.get("year") or ""
        if title_key:
            by_title_year.setdefault(f"{title_key}:{year}", []).append(detail)

    groups: list[dict[str, Any]] = []
    seen_ids: set[tuple[int, ...]] = set()
    for reason, source in (("DOI", by_doi), ("标题+年份", by_title_year)):
        for key, items in source.items():
            if len(items) < 2:
                continue
            item_ids = tuple(sorted(int(item["id"]) for item in items))
            if item_ids in seen_ids:
                continue
            seen_ids.add(item_ids)
            groups.append({"reason": reason, "key": key, "items": items})
    groups.sort(key=lambda item: (item["reason"], item["key"]))
    return groups


def _merged_payload(primary: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, Any]:
    payload = dict(primary)
    text_fields = [
        "translated_title",
        "publication_title",
        "publisher",
        "school",
        "conference_name",
        "standard_number",
        "patent_number",
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
        "remarks",
        "cite_key",
    ]
    for other in others:
        for field in text_fields:
            if not payload.get(field) and other.get(field):
                payload[field] = other.get(field)
        if not payload.get("year") and other.get("year"):
            payload["year"] = other.get("year")
        if not payload.get("rating") and other.get("rating"):
            payload["rating"] = other.get("rating")

    authors: list[str] = []
    tags: list[str] = []
    for item in [primary] + others:
        for author in item.get("authors", []):
            if author not in authors:
                authors.append(author)
        for tag in item.get("tags", []):
            if tag not in tags:
                tags.append(tag)
    payload["authors"] = authors
    payload["tags"] = tags
    return payload


def merge_literatures(database: LibraryDatabase, primary_id: int, merged_ids: list[int], reason: str) -> None:
    primary = database.get_literature(primary_id)
    if not primary:
        raise ValueError("主文献不存在")

    others = [database.get_literature(item_id) for item_id in merged_ids]
    others = [item for item in others if item]
    if not others:
        return

    payload = _merged_payload(primary, others)
    database.save_literature(payload)

    for other in others:
        other_id = int(other["id"])
        database.connection.execute("UPDATE attachments SET literature_id = ? WHERE literature_id = ?", (primary_id, other_id))
        database.connection.execute("UPDATE notes SET literature_id = ? WHERE literature_id = ?", (primary_id, other_id))
        database.record_merge_history(
            primary_id,
            other_id,
            reason,
            {
                "primary_title": primary.get("title", ""),
                "merged_title": other.get("title", ""),
                "merged_attachments": len(other.get("attachments", [])),
                "merged_notes": len(other.get("notes", [])),
            },
        )
        database.connection.execute("DELETE FROM literature_fts WHERE literature_id = ?", (other_id,))
        database.connection.execute("DELETE FROM literatures WHERE id = ?", (other_id,))

    database.connection.commit()
    database.refresh_search_index_for_literature(primary_id)
    database.connection.commit()
