from __future__ import annotations

from pathlib import Path

from .config import AppSettings
from .db import LibraryDatabase
from .metadata_service import scan_file


SUPPORTED_IMPORT_EXTENSIONS = {".pdf", ".bib", ".ris", ".docx", ".md", ".markdown", ".txt"}


def iter_supported_files(paths: list[str], recursive: bool = True) -> list[Path]:
    files: list[Path] = []
    for item in paths:
        path = Path(item).expanduser().resolve()
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            files.extend(file for file in iterator if file.is_file() and file.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS)
        elif path.is_file() and path.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS:
            files.append(path)
    unique: dict[str, Path] = {}
    for file in files:
        unique[str(file)] = file
    return list(unique.values())


def scan_import_sources(paths: list[str], recursive: bool = True) -> list[dict]:
    items: list[dict] = []
    for file_path in iter_supported_files(paths, recursive=recursive):
        for item in scan_file(file_path):
            payload = item["payload"]
            items.append(
                {
                    "selected": True,
                    "kind": item["kind"],
                    "source_path": item["source_path"],
                    "display_title": item["display_title"],
                    "role": item["role"],
                    "entry_type": payload.get("entry_type", "misc"),
                    "authors": payload.get("authors", []),
                    "year": payload.get("year"),
                    "payload": payload,
                }
            )
    return items


def import_scanned_items(
    database: LibraryDatabase,
    items: list[dict],
    settings: AppSettings,
    import_mode: str | None = None,
) -> dict[str, int]:
    imported = 0
    skipped = 0
    selected_mode = import_mode or settings.default_import_mode

    for item in items:
        if not item.get("selected", True):
            skipped += 1
            continue

        payload = dict(item.get("payload", {}))
        source_path = item["source_path"]
        source_kind = item["kind"]

        if source_kind == "reference_record":
            literature_id = database.save_literature(payload)
        elif source_kind == "file_record":
            literature_id = database.save_literature(payload)
            database.add_attachments(
                literature_id,
                [source_path],
                role=item.get("role", "source") or "source",
                language=payload.get("language", ""),
                import_mode=selected_mode,
                is_primary=True,
            )
        elif source_kind == "note_record":
            literature_id = database.save_literature(payload)
            database.save_note(
                literature_id=literature_id,
                title=payload.get("title", Path(source_path).stem),
                content="",
                attachment_ids=[],
                note_type="file",
                note_format=payload.get("note_format", "other"),
                external_file_path=source_path,
                import_mode=selected_mode,
            )
        else:
            skipped += 1
            continue

        database.record_import_history(source_path, source_kind, literature_id, payload)
        imported += 1

    database.rebuild_search_index()
    return {"imported": imported, "skipped": skipped}
