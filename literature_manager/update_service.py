from __future__ import annotations

import json
import os
import re
from html import unescape
from pathlib import Path
from urllib import error, parse, request


GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
GITHUB_RELEASES_LATEST = "https://github.com/{repo}/releases/latest"
HTTP_HEADERS = {
    "User-Agent": "LiteratureManagementTool/0.3.2",
    "Accept": "application/vnd.github+json",
}


def _request_headers(*, html_mode: bool = False) -> dict[str, str]:
    headers = dict(HTTP_HEADERS)
    if html_mode:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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
    req = request.Request(url, headers=_request_headers())
    with request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(url: str) -> str:
    req = request.Request(url, headers=_request_headers(html_mode=True))
    with request.urlopen(req, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


def _select_setup_asset(assets: list[dict]) -> dict | None:
    setup_asset = None
    for item in assets:
        name = str(item.get("name", ""))
        if name.lower().endswith(".exe") and "setup" in name.lower():
            setup_asset = item
            break
    if setup_asset is None and assets:
        setup_asset = assets[0]
    return setup_asset


def _build_release_payload(repository: str, current_version: str, payload: dict, *, source: str) -> dict:
    assets = payload.get("assets", [])
    setup_asset = _select_setup_asset(assets)
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
        "update_lookup_source": source,
    }


def _clean_html_text(fragment: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _extract_tag_from_release_url(url: str) -> str:
    match = re.search(r"/releases/tag/([^/?#]+)", url)
    return match.group(1).strip() if match else ""


def _extract_published_at_from_html(document: str) -> str:
    match = re.search(r'datetime="([^"]+)"', document)
    return match.group(1).strip() if match else ""


def _extract_release_body_from_html(document: str) -> str:
    match = re.search(r'(?is)<div[^>]*class="[^"]*markdown-body[^"]*"[^>]*>(.*?)</div>', document)
    if not match:
        return ""
    return _clean_html_text(match.group(1))


def _extract_release_name_from_html(document: str, default_value: str) -> str:
    match = re.search(r'(?is)<h1[^>]*>\s*<a[^>]*>(.*?)</a>', document)
    if not match:
        return default_value
    text = _clean_html_text(match.group(1))
    return text or default_value


def _extract_expanded_assets_url(document: str, base_url: str) -> str:
    match = re.search(r'src="([^"]*/releases/expanded_assets/[^"]+)"', document)
    if not match:
        return ""
    return parse.urljoin(base_url, unescape(match.group(1)))


def _extract_setup_asset_from_fragment(fragment: str, tag: str) -> dict | None:
    pattern = rf'href="([^"]*/releases/download/{re.escape(tag)}/[^"]+)"'
    matches = re.findall(pattern, fragment)
    if not matches:
        return None
    assets: list[dict] = []
    for href in matches:
        asset_url = parse.urljoin("https://github.com", unescape(href))
        asset_name = unescape(asset_url.rsplit("/", 1)[-1])
        assets.append({"name": asset_name, "browser_download_url": asset_url})
    return _select_setup_asset(assets)


def _check_latest_release_via_web(repository: str, current_version: str) -> dict:
    latest_url = GITHUB_RELEASES_LATEST.format(repo=repository)
    req = request.Request(latest_url, headers=_request_headers(html_mode=True))
    with request.urlopen(req, timeout=20) as response:
        final_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
        document = response.read().decode(charset, errors="ignore")

    tag = _extract_tag_from_release_url(final_url)
    if not tag:
        raise ValueError("无法解析最新版本标签。")
    latest_version = tag.lstrip("v") or "0.0.0"
    release_name = _extract_release_name_from_html(document, f"v{latest_version}")
    published_at = _extract_published_at_from_html(document)
    body = _extract_release_body_from_html(document)

    asset = None
    expanded_assets_url = _extract_expanded_assets_url(document, final_url)
    if expanded_assets_url:
        fragment = _get_text(expanded_assets_url)
        asset = _extract_setup_asset_from_fragment(fragment, tag)
    if asset is None:
        guessed_name = f"Literature-management-tool-v{latest_version}-Setup.exe"
        guessed_url = f"https://github.com/{repository}/releases/download/{tag}/{guessed_name}"
        asset = {"name": guessed_name, "browser_download_url": guessed_url}

    payload = {
        "tag_name": tag,
        "name": release_name,
        "published_at": published_at,
        "html_url": final_url,
        "body": body,
        "assets": [asset],
    }
    return _build_release_payload(repository, current_version, payload, source="web")


def _fallback_release_from_web(repository: str, current_version: str, notice: str) -> dict | None:
    try:
        fallback = _check_latest_release_via_web(repository, current_version)
    except (ValueError, error.HTTPError, error.URLError):
        return None
    fallback["update_lookup_notice"] = notice
    return fallback


def check_latest_release(repo: str, current_version: str) -> dict:
    repository = repo.strip()
    if not repository:
        raise ValueError("GitHub 仓库配置为空。")

    try:
        payload = _get_json(GITHUB_API.format(repo=repository))
        return _build_release_payload(repository, current_version, payload, source="api")
    except error.HTTPError as exc:
        if exc.code in {403, 429}:
            fallback = _fallback_release_from_web(
                repository,
                current_version,
                f"GitHub API 返回 HTTP {exc.code}，已自动回退到网页解析。",
            )
            if fallback is not None:
                return fallback
        raise ValueError(f"检查更新失败：HTTP {exc.code}") from exc
    except error.URLError as exc:
        fallback = _fallback_release_from_web(
            repository,
            current_version,
            "GitHub API 请求失败，已自动回退到网页解析。",
        )
        if fallback is not None:
            return fallback
        raise ValueError(f"检查更新失败：{exc.reason}") from exc


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
