from __future__ import annotations

import os
import subprocess
from pathlib import Path


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
    target = Path(path)
    if os.name == "nt":
        if target.exists() and target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            browse_target = target.parent if target.suffix else target
            subprocess.Popen(["explorer", str(browse_target)])
        return
    open_path(str(target.parent if target.is_file() else target))
