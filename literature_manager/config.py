from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .table_columns import (
    DEFAULT_LITERATURE_COLUMN_KEYS,
    normalize_literature_column_keys,
    normalize_literature_column_widths,
)

APP_NAME = "Literature management tool"
APP_DISPLAY_NAME = "文献管理工具"
ENV_HOME = "LITERATURE_MANAGER_HOME"
DEFAULT_LIBRARY_NAME = "默认文献库"
DEFAULT_LIBRARY_SLUG = "default-library"
DEFAULT_UPDATE_REPO = "Dongyurocket/literature-management-tool"
DEFAULT_UMI_OCR_REPO = "hiroi-sora/Umi-OCR"
DEFAULT_METADATA_SOURCES = [
    "crossref",
    "datacite",
    "openalex",
    "cnki",
    "ustc_openurl",
    "tsinghua_openurl",
    "openlibrary",
    "googlebooks",
]


def resolve_app_home() -> Path:
    override = os.getenv(ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def resolve_tools_dir() -> Path:
    tools_dir = resolve_app_home() / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    return tools_dir


@dataclass(slots=True)
class LibraryProfile:
    name: str
    slug: str
    archived: bool = False


@dataclass
class AppSettings:
    library_root: str = ""
    default_import_mode: str = "copy"
    recent_export_dir: str = ""
    pdf_reader_path: str = ""
    ui_theme: str = "system"
    umi_ocr_path: str = ""
    umi_ocr_repo: str = DEFAULT_UMI_OCR_REPO
    umi_ocr_variant: str = "rapid"
    umi_ocr_command: str = ""
    umi_ocr_timeout_sec: int = 180
    update_repo: str = DEFAULT_UPDATE_REPO
    metadata_sources: list[str] = field(default_factory=lambda: list(DEFAULT_METADATA_SOURCES))
    preferred_export_template: str = "markdown_report"
    detail_autosave_enabled: bool = True
    detail_autosave_interval_sec: int = 2
    list_columns: list[str] = field(default_factory=lambda: list(DEFAULT_LITERATURE_COLUMN_KEYS))
    list_column_widths: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.detail_autosave_enabled = bool(self.detail_autosave_enabled)
        try:
            interval_sec = int(self.detail_autosave_interval_sec)
        except (TypeError, ValueError):
            interval_sec = 2
        self.detail_autosave_interval_sec = min(max(interval_sec, 1), 300)
        self.list_columns = normalize_literature_column_keys(self.list_columns)
        self.list_column_widths = normalize_literature_column_widths(self.list_column_widths)


class SettingsStore:
    def __init__(self) -> None:
        self.base_dir = self._resolve_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.base_dir / "library_registry.json"
        self.settings_path = self.base_dir / "settings.json"
        self.database_path = self.base_dir / "library.sqlite3"
        self._ensure_registry()

    @staticmethod
    def _resolve_base_dir() -> Path:
        return resolve_app_home()

    def _default_registry(self) -> dict:
        return {
            "current_profile": DEFAULT_LIBRARY_NAME,
            "profiles": [asdict(LibraryProfile(name=DEFAULT_LIBRARY_NAME, slug=DEFAULT_LIBRARY_SLUG))],
        }

    def _default_library_root(self, slug: str) -> str:
        return str(self._profile_dir(slug) / "library_files")

    def _load_registry(self) -> dict:
        if not self.registry_path.exists():
            return self._default_registry()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._default_registry()
        if not isinstance(payload, dict):
            return self._default_registry()
        profiles = payload.get("profiles", [])
        if not isinstance(profiles, list) or not profiles:
            return self._default_registry()
        if not payload.get("current_profile"):
            payload["current_profile"] = profiles[0]["name"]
        return payload

    def _save_registry(self, payload: dict) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _profile_dir(self, slug: str) -> Path:
        return self.base_dir / "profiles" / slug

    def _migrate_legacy_single_library(self, profile_dir: Path) -> None:
        legacy_settings = self.base_dir / "settings.json"
        legacy_database = self.base_dir / "library.sqlite3"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_settings = profile_dir / "settings.json"
        profile_database = profile_dir / "library.sqlite3"
        if legacy_settings.exists() and legacy_settings != profile_settings and not profile_settings.exists():
            shutil.move(str(legacy_settings), str(profile_settings))
        if legacy_database.exists() and legacy_database != profile_database and not profile_database.exists():
            shutil.move(str(legacy_database), str(profile_database))

    def _ensure_registry(self) -> None:
        registry = self._load_registry()
        profiles = registry.get("profiles", [])
        if not profiles:
            registry = self._default_registry()
            profiles = registry["profiles"]

        first_profile = profiles[0]
        self._migrate_legacy_single_library(self._profile_dir(first_profile["slug"]))
        for profile in profiles:
            self._profile_dir(profile["slug"]).mkdir(parents=True, exist_ok=True)

        self._save_registry(registry)
        self._sync_paths_from_registry(registry)

    def _sync_paths_from_registry(self, registry: dict | None = None) -> None:
        payload = registry or self._load_registry()
        profiles = {item["name"]: item for item in payload.get("profiles", [])}
        current_name = payload.get("current_profile") or DEFAULT_LIBRARY_NAME
        profile = profiles.get(current_name)
        if profile is None:
            profile = next(iter(profiles.values()))
            payload["current_profile"] = profile["name"]
            self._save_registry(payload)
        profile_dir = self._profile_dir(profile["slug"])
        profile_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = profile_dir / "settings.json"
        self.database_path = profile_dir / "library.sqlite3"

    def _slugify(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if slug:
            return slug
        existing = {item["slug"] for item in self._load_registry().get("profiles", [])}
        index = 1
        while True:
            candidate = f"library-{index}"
            if candidate not in existing:
                return candidate
            index += 1

    def current_profile(self) -> LibraryProfile:
        registry = self._load_registry()
        current_name = registry.get("current_profile") or DEFAULT_LIBRARY_NAME
        for item in registry.get("profiles", []):
            if item["name"] == current_name:
                return LibraryProfile(**item)
        return LibraryProfile(name=DEFAULT_LIBRARY_NAME, slug=DEFAULT_LIBRARY_SLUG)

    def list_profiles(self, *, include_archived: bool = True) -> list[LibraryProfile]:
        profiles = [LibraryProfile(**item) for item in self._load_registry().get("profiles", [])]
        if include_archived:
            return profiles
        return [item for item in profiles if not item.archived]

    def create_profile(self, name: str, *, template_settings: AppSettings | None = None) -> LibraryProfile:
        profile_name = name.strip()
        if not profile_name:
            raise ValueError("文库名称不能为空。")

        registry = self._load_registry()
        existing_names = {item["name"] for item in registry.get("profiles", [])}
        if profile_name in existing_names:
            raise ValueError("已存在同名文库。")

        slug = self._slugify(profile_name)
        existing_slugs = {item["slug"] for item in registry.get("profiles", [])}
        if slug in existing_slugs:
            suffix = 2
            while f"{slug}-{suffix}" in existing_slugs:
                suffix += 1
            slug = f"{slug}-{suffix}"

        profile = LibraryProfile(name=profile_name, slug=slug)
        registry.setdefault("profiles", []).append(asdict(profile))
        self._save_registry(registry)

        profile_dir = self._profile_dir(slug)
        profile_dir.mkdir(parents=True, exist_ok=True)
        settings_path = profile_dir / "settings.json"
        if not settings_path.exists():
            settings = AppSettings(library_root=self._default_library_root(slug))
            if template_settings is not None:
                settings = AppSettings(
                    **{
                        **asdict(template_settings),
                        "library_root": self._default_library_root(slug),
                    }
                )
            current_settings_path = self.settings_path
            current_database_path = self.database_path
            self.settings_path = settings_path
            self.database_path = profile_dir / "library.sqlite3"
            self.save(settings)
            self.settings_path = current_settings_path
            self.database_path = current_database_path
        return profile

    def switch_profile(self, name: str) -> LibraryProfile:
        registry = self._load_registry()
        for profile in registry.get("profiles", []):
            if profile["name"] != name:
                continue
            registry["current_profile"] = name
            self._save_registry(registry)
            self._sync_paths_from_registry(registry)
            return LibraryProfile(**profile)
        raise ValueError("未找到指定文库。")

    def set_profile_archived(self, name: str, archived: bool) -> LibraryProfile:
        registry = self._load_registry()
        active_profiles = [item for item in registry.get("profiles", []) if not item.get("archived")]
        for profile in registry.get("profiles", []):
            if profile["name"] != name:
                continue
            if archived and not profile.get("archived") and len(active_profiles) <= 1:
                raise ValueError("至少需要保留一个未归档文库。")
            profile["archived"] = bool(archived)
            if archived and registry.get("current_profile") == name:
                for candidate in registry.get("profiles", []):
                    if not candidate.get("archived") and candidate["name"] != name:
                        registry["current_profile"] = candidate["name"]
                        break
            self._save_registry(registry)
            self._sync_paths_from_registry(registry)
            return LibraryProfile(**profile)
        raise ValueError("未找到指定文库。")

    def profile_summary(self, *, include_archived: bool = True) -> list[dict[str, str | bool]]:
        current_name = self.current_profile().name
        summaries: list[dict[str, str | bool]] = []
        for profile in self.list_profiles(include_archived=include_archived):
            profile_dir = self._profile_dir(profile.slug)
            settings_path = profile_dir / "settings.json"
            settings = AppSettings(library_root=self._default_library_root(profile.slug))
            if settings_path.exists():
                try:
                    payload = json.loads(settings_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    payload = {}
                settings = AppSettings(**{**asdict(AppSettings()), **payload})
            summaries.append(
                {
                    "name": profile.name,
                    "slug": profile.slug,
                    "archived": profile.archived,
                    "active": profile.name == current_name,
                    "database_path": str(profile_dir / "library.sqlite3"),
                    "settings_path": str(settings_path),
                    "library_root": settings.library_root,
                }
            )
        return summaries

    def load(self) -> AppSettings:
        if not self.settings_path.exists():
            return AppSettings(library_root=str(self.settings_path.parent / "library_files"))
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return AppSettings(library_root=str(self.settings_path.parent / "library_files"))
        return AppSettings(**{**asdict(AppSettings()), **payload})

    def save(self, settings: AppSettings) -> None:
        payload = asdict(settings)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
