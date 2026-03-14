from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from .config import AppSettings, SettingsStore
from .db import LibraryDatabase
from .utils import now_text


def find_missing_paths(database: LibraryDatabase) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    rows = database._fetchall("SELECT * FROM attachments ORDER BY id")
    for row in rows:
        payload = dict(row)
        resolved = database.resolve_path(payload["file_path"], payload["is_relative"])
        if not resolved.exists():
            payload["resolved_path"] = str(resolved)
            missing.append(payload)
    notes = database._fetchall("SELECT * FROM notes WHERE note_type = 'file' ORDER BY id")
    for row in notes:
        payload = dict(row)
        if payload.get("external_path"):
            resolved = database.resolve_path(payload["external_path"], payload.get("external_is_relative", 0))
            if not resolved.exists():
                payload["resolved_path"] = str(resolved)
                payload["kind"] = "note"
                missing.append(payload)
    return missing


def repair_missing_paths(database: LibraryDatabase, search_root: str) -> dict[str, int]:
    root = Path(search_root).expanduser().resolve()
    if not root.exists():
        raise ValueError("修复目录不存在")
    fixed = 0
    unresolved = 0
    for item in find_missing_paths(database):
        target_name = Path(item["resolved_path"]).name
        matches = list(root.rglob(target_name))
        if len(matches) != 1:
            unresolved += 1
            continue
        match = matches[0].resolve()
        stored, is_relative = database._store_path(match)
        if item.get("kind") == "note":
            database.connection.execute(
                "UPDATE notes SET external_path = ?, external_is_relative = ? WHERE id = ?",
                (stored, is_relative, item["id"]),
            )
            literature_id = item["literature_id"]
        else:
            database.connection.execute(
                "UPDATE attachments SET file_path = ?, is_relative = ? WHERE id = ?",
                (stored, is_relative, item["id"]),
            )
            literature_id = item["literature_id"]
        database.refresh_search_index_for_literature(int(literature_id))
        fixed += 1
    database.connection.commit()
    return {"fixed": fixed, "unresolved": unresolved}


def create_backup(settings_store: SettingsStore, settings: AppSettings, destination: str, include_library: bool = True) -> str:
    destination_path = Path(destination).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    library_root = Path(settings.library_root).expanduser().resolve() if settings.library_root else None
    manifest = {
        "created_at": now_text(),
        "database": settings_store.database_path.name,
        "settings": settings_store.settings_path.name,
        "library_root": str(library_root) if library_root and library_root.exists() else "",
        "include_library": bool(include_library and library_root and library_root.exists()),
    }
    with zipfile.ZipFile(destination_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if settings_store.database_path.exists():
            archive.write(settings_store.database_path, arcname="library.sqlite3")
        if settings_store.settings_path.exists():
            archive.write(settings_store.settings_path, arcname="settings.json")
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        if manifest["include_library"] and library_root is not None:
            for path in library_root.rglob("*"):
                if path.is_file():
                    archive.write(path, arcname=str(Path("library_files") / path.relative_to(library_root)))
    return str(destination_path)


def restore_backup(settings_store: SettingsStore, backup_path: str) -> AppSettings:
    archive_path = Path(backup_path).expanduser().resolve()
    if not archive_path.exists():
        raise ValueError("备份文件不存在")

    restore_root = settings_store.base_dir / "restored_library"
    if restore_root.exists():
        shutil.rmtree(restore_root)
    restore_root.mkdir(parents=True, exist_ok=True)

    extracted_file_members: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as archive:
        if "library.sqlite3" not in archive.namelist() or "settings.json" not in archive.namelist():
            raise ValueError("备份文件缺少必要的数据库或设置文件。")
        archive.extract("library.sqlite3", settings_store.base_dir)
        archive.extract("settings.json", settings_store.base_dir)
        file_members = [name for name in archive.namelist() if name.startswith("library_files/")]
        for member in file_members:
            archive.extract(member, restore_root)
        extracted_file_members = file_members

    settings = settings_store.load()
    if any((restore_root / member).exists() for member in extracted_file_members):
        settings.library_root = str(restore_root / "library_files")
        settings_store.save(settings)
    return settings
