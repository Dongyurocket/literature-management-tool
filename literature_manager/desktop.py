from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

_logger = logging.getLogger(__name__)


def open_path(path: str, preferred_app: str = "") -> None:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    if preferred_app:
        preferred = Path(preferred_app)
        if not preferred.exists():
            raise FileNotFoundError(preferred_app)
        subprocess.Popen([str(preferred), str(target)])
        return
    if os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", str(target)])


def open_parent_folder(path: str) -> None:
    """Open the parent folder of *path* in the system file manager."""
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(path)
    target = target.resolve()
    folder = target.parent if target.is_file() else target
    _logger.debug("open_parent_folder: %s", folder)
    open_path(str(folder))
