from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request


GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
HTTP_HEADERS = {
    "User-Agent": "LiteratureManagementTool/0.3.1",
    "Accept": "application/vnd.github+json",
}


def _normalize_version(value: str) -> tuple[int, ...]:
    text = (value or "").strip().lower().lstrip("v")
    parts: list[int] = []
    for chunk in text.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _get_json(url: str) -> dict:
    req = request.Request(url, headers=HTTP_HEADERS)
    with request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def check_latest_release(repo: str, current_version: str) -> dict:
    repository = repo.strip()
    if not repository:
        raise ValueError("GitHub 仓库配置为空。")

    try:
        payload = _get_json(GITHUB_API.format(repo=repository))
    except error.HTTPError as exc:
        raise ValueError(f"检查更新失败：HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise ValueError(f"检查更新失败：{exc.reason}") from exc

    assets = payload.get("assets", [])
    setup_asset = None
    for item in assets:
        name = str(item.get("name", ""))
        if name.lower().endswith(".exe") and "setup" in name.lower():
            setup_asset = item
            break
    if setup_asset is None and assets:
        setup_asset = assets[0]

    latest_version = str(payload.get("tag_name", "")).lstrip("v") or "0.0.0"
    return {
        "repo": repository,
        "current_version": current_version,
        "latest_version": latest_version,
        "is_update_available": _normalize_version(latest_version) > _normalize_version(current_version),
        "release_name": payload.get("name", "") or f"v{latest_version}",
        "published_at": payload.get("published_at", ""),
        "body": payload.get("body", ""),
        "html_url": payload.get("html_url", ""),
        "asset_name": setup_asset.get("name", "") if setup_asset else "",
        "asset_url": setup_asset.get("browser_download_url", "") if setup_asset else "",
    }


def download_release_asset(url: str, destination: str | Path) -> str:
    download_url = str(url).strip()
    if not download_url:
        raise ValueError("更新包下载地址为空。")

    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(download_url, headers={"User-Agent": HTTP_HEADERS["User-Agent"]})
    try:
        with request.urlopen(req, timeout=120) as response, target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    except error.HTTPError as exc:
        raise ValueError(f"下载更新失败：HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise ValueError(f"下载更新失败：{exc.reason}") from exc
    return str(target)
