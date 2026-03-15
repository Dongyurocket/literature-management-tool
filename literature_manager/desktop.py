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


def reveal_path(path: str) -> None:
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(path)
    target = target.resolve()
    if os.name == "nt":
        normalized = os.path.normpath(str(target))
        if target.is_file():
            cmd = f'explorer /select,"{normalized}"'
            _logger.debug("reveal_path command: %s", cmd)
            subprocess.Popen(cmd)
        else:
            subprocess.Popen(["explorer", normalized])
        return
    open_path(str(target.parent if target.is_file() else target))
