from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

APP_NAME = "Literature management tool"
ENV_HOME = "LITERATURE_MANAGER_HOME"


@dataclass
class AppSettings:
    library_root: str = ""
    default_import_mode: str = "copy"
    recent_export_dir: str = ""
    pdf_reader_path: str = ""


class SettingsStore:
    def __init__(self) -> None:
        self.base_dir = self._resolve_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.base_dir / "settings.json"
        self.database_path = self.base_dir / "library.sqlite3"

    @staticmethod
    def _resolve_base_dir() -> Path:
        override = os.getenv(ENV_HOME)
        if override:
            return Path(override).expanduser().resolve()
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / f".{APP_NAME.lower()}"

    def load(self) -> AppSettings:
        if not self.settings_path.exists():
            return AppSettings()
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return AppSettings()
        return AppSettings(**{**asdict(AppSettings()), **payload})

    def save(self, settings: AppSettings) -> None:
        self.settings_path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
