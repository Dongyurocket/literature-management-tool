from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib import error, request

from .config import AppSettings, DEFAULT_UMI_OCR_REPO, resolve_tools_dir

GITHUB_RELEASE_API = "https://api.github.com/repos/{repo}/releases/latest"
HTTP_HEADERS = {
    "User-Agent": "LiteratureManagementTool/0.3.2",
    "Accept": "application/vnd.github+json",
}
DEFAULT_UMI_PORT = 1224
UMI_VARIANTS = ("rapid", "paddle")


def _normalize_variant(value: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in UMI_VARIANTS else "rapid"


def _get_json(url: str) -> dict:
    req = request.Request(url, headers=HTTP_HEADERS)
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_file(url: str, destination: str | Path) -> Path:
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(url, headers={"User-Agent": HTTP_HEADERS["User-Agent"]})
    with request.urlopen(req, timeout=300) as response, target.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    return target


def _extract_self_extracting_archive(archive_path: str | Path, destination_dir: str | Path) -> Path:
    archive = Path(archive_path).expanduser().resolve()
    destination = Path(destination_dir).expanduser().resolve()
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [str(archive), "-y", f"-o{destination}"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        error_text = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(error_text or "Umi-OCR 安装包解压失败。")
    return destination


def _tools_root() -> Path:
    root = resolve_tools_dir() / "umi-ocr"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _managed_install_root(variant: str) -> Path:
    return _tools_root() / _normalize_variant(variant)


def _find_umi_executable(base_dir: str | Path) -> Path | None:
    root = Path(base_dir).expanduser().resolve()
    if root.is_file():
        return root if root.name.lower() == "umi-ocr.exe" else None
    if not root.exists():
        return None
    direct = root / "Umi-OCR.exe"
    if direct.exists():
        return direct
    for candidate in root.rglob("Umi-OCR.exe"):
        if candidate.is_file():
            return candidate.resolve()
    return None


def resolve_umi_ocr_path(settings: AppSettings) -> Path | None:
    configured = (settings.umi_ocr_path or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            found = _find_umi_executable(candidate)
            if found:
                return found
        elif candidate.is_file():
            return candidate.resolve()

    preferred_variant = _normalize_variant(settings.umi_ocr_variant)
    search_order = [preferred_variant] + [item for item in UMI_VARIANTS if item != preferred_variant]
    for variant in search_order:
        found = _find_umi_executable(_managed_install_root(variant))
        if found:
            return found
    return None


def has_ocr_config(settings: AppSettings) -> bool:
    if (settings.umi_ocr_command or "").strip():
        return True
    return resolve_umi_ocr_path(settings) is not None


def select_umi_ocr_asset(assets: list[dict], preferred_variant: str) -> dict:
    variant = _normalize_variant(preferred_variant)
    normalized_assets = [item for item in assets if str(item.get("name", "")).lower().endswith(".exe")]
    if not normalized_assets:
        raise ValueError("Umi-OCR 最新发布未提供 Windows 安装包。")

    preferred_tokens = [f"umi-ocr_{variant}_", variant]
    fallback_tokens = [token for token in UMI_VARIANTS if token != variant]

    for asset in normalized_assets:
        name = str(asset.get("name", "")).lower()
        if all(token in name for token in preferred_tokens[:1]):
            return asset
    for asset in normalized_assets:
        name = str(asset.get("name", "")).lower()
        if any(token in name for token in fallback_tokens):
            return asset
    return normalized_assets[0]


def install_umi_ocr(settings: AppSettings) -> dict:
    repo = (settings.umi_ocr_repo or DEFAULT_UMI_OCR_REPO).strip() or DEFAULT_UMI_OCR_REPO
    variant = _normalize_variant(settings.umi_ocr_variant)

    try:
        payload = _get_json(GITHUB_RELEASE_API.format(repo=repo))
    except error.HTTPError as exc:
        raise ValueError(f"获取 Umi-OCR 发布信息失败：HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise ValueError(f"获取 Umi-OCR 发布信息失败：{exc.reason}") from exc

    asset = select_umi_ocr_asset(payload.get("assets", []), variant)
    download_url = str(asset.get("browser_download_url") or asset.get("url") or "").strip()
    if not download_url:
        raise ValueError("Umi-OCR 发布包缺少下载地址。")

    downloads_dir = _tools_root() / "downloads"
    archive_path = _download_file(download_url, downloads_dir / str(asset["name"]))
    install_root = _managed_install_root(variant)
    extracted_root = _extract_self_extracting_archive(archive_path, install_root)
    executable_path = _find_umi_executable(extracted_root)
    if executable_path is None:
        raise ValueError("已下载 Umi-OCR，但未找到 Umi-OCR.exe。")

    settings.umi_ocr_repo = repo
    settings.umi_ocr_variant = variant
    settings.umi_ocr_path = str(executable_path)
    return {
        "repo": repo,
        "variant": variant,
        "release_name": payload.get("name", "") or payload.get("tag_name", ""),
        "release_tag": payload.get("tag_name", ""),
        "asset_name": asset.get("name", ""),
        "install_dir": str(executable_path.parent),
        "executable_path": str(executable_path),
    }


def read_umi_ocr_server_port(umi_executable: str | Path) -> int:
    exe_path = Path(umi_executable).expanduser().resolve()
    settings_path = exe_path.parent / "UmiOCR-data" / ".pre_settings"
    if not settings_path.exists():
        return DEFAULT_UMI_PORT
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
        port = int(payload.get("server_port", DEFAULT_UMI_PORT))
        return port if 0 < port < 65536 else DEFAULT_UMI_PORT
    except (OSError, ValueError, json.JSONDecodeError):
        return DEFAULT_UMI_PORT


def _umi_base_url(umi_executable: str | Path) -> str:
    return f"http://127.0.0.1:{read_umi_ocr_server_port(umi_executable)}"


def _probe_umi_service(umi_executable: str | Path) -> str | None:
    base_url = _umi_base_url(umi_executable)
    try:
        _get_json(f"{base_url}/api/doc/get_options")
    except Exception:
        return None
    return base_url


def _launch_umi_ocr(umi_executable: str | Path) -> None:
    exe_path = Path(umi_executable).expanduser().resolve()
    creationflags = 0
    if os.name == "nt":
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [str(exe_path)],
        cwd=str(exe_path.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _ensure_umi_service(umi_executable: str | Path, timeout_sec: int) -> str:
    ready_url = _probe_umi_service(umi_executable)
    if ready_url:
        return ready_url

    _launch_umi_ocr(umi_executable)
    deadline = time.monotonic() + max(30, int(timeout_sec))
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            ready_url = _probe_umi_service(umi_executable)
        except Exception as exc:  # pragma: no cover - _probe_umi_service already swallows
            last_error = exc
            ready_url = None
        if ready_url:
            return ready_url
        time.sleep(1)
    raise ValueError("Umi-OCR 未能在限定时间内启动 HTTP 服务。") from last_error


def _build_multipart_request(
    url: str,
    *,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> request.Request:
    boundary = f"----LiteratureManager{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    safe_name = f"upload{file_path.suffix.lower()}"
    file_bytes = file_path.read_bytes()
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{safe_name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    body = b"".join(chunks)
    return request.Request(
        url,
        data=body,
        headers={
            "User-Agent": HTTP_HEADERS["User-Agent"],
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )


def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "User-Agent": HTTP_HEADERS["User-Agent"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _clear_umi_doc_task(base_url: str, task_id: str) -> None:
    try:
        request.urlopen(
            request.Request(
                f"{base_url}/api/doc/clear/{task_id}",
                headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
            ),
            timeout=10,
        ).read()
    except Exception:
        return


def _run_umi_doc_ocr(path: Path, umi_executable: str | Path, timeout_sec: int) -> str:
    base_url = _ensure_umi_service(umi_executable, timeout_sec)
    upload_request = _build_multipart_request(
        f"{base_url}/api/doc/upload",
        fields={
            "json": json.dumps(
                {
                    "doc.extractionMode": "mixed",
                    "tbpu.parser": "multi_para",
                },
                ensure_ascii=False,
            )
        },
        file_field="file",
        file_path=path,
    )
    try:
        with request.urlopen(upload_request, timeout=120) as response:
            upload_payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise ValueError(f"Umi-OCR 上传文件失败：HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise ValueError(f"Umi-OCR 上传文件失败：{exc.reason}") from exc

    if int(upload_payload.get("code", 0) or 0) != 100:
        raise ValueError(upload_payload.get("message") or "Umi-OCR 未能创建识别任务。")

    task_id = str(upload_payload.get("data", "")).strip()
    if not task_id:
        raise ValueError("Umi-OCR 未返回有效任务 ID。")

    deadline = time.monotonic() + max(30, int(timeout_sec))
    fragments: list[str] = []
    try:
        while time.monotonic() < deadline:
            payload = _post_json(
                f"{base_url}/api/doc/result",
                {
                    "id": task_id,
                    "is_data": True,
                    "format": "text",
                    "is_unread": True,
                },
            )
            if int(payload.get("code", 0) or 0) != 100:
                raise ValueError(payload.get("message") or "Umi-OCR 返回了异常状态。")

            chunk = str(payload.get("data") or "").strip()
            if chunk:
                fragments.append(chunk)

            if payload.get("is_done"):
                if payload.get("state") != "success":
                    raise ValueError(payload.get("message") or "Umi-OCR 任务执行失败。")
                if fragments:
                    return "\n\n".join(item for item in fragments if item).strip()
                payload = _post_json(
                    f"{base_url}/api/doc/result",
                    {
                        "id": task_id,
                        "is_data": True,
                        "format": "text",
                        "is_unread": False,
                    },
                )
                final_text = str(payload.get("data") or "").strip()
                if final_text:
                    return final_text
                raise ValueError("Umi-OCR 已完成，但没有返回可用文本。")
            time.sleep(1)
    finally:
        _clear_umi_doc_task(base_url, task_id)
    raise TimeoutError("等待 Umi-OCR 识别结果超时。")


def _run_command_ocr(path: Path, settings: AppSettings) -> str:
    with tempfile.TemporaryDirectory(prefix="literature_manager_ocr_") as temp_dir:
        output_path = Path(temp_dir) / "ocr_result.txt"
        command = settings.umi_ocr_command.format(
            input=str(path),
            output=str(output_path),
            umi_ocr=(settings.umi_ocr_path or "").strip(),
        )
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(30, int(settings.umi_ocr_timeout_sec)),
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(stderr or "OCR 命令执行失败。")

        if output_path.exists():
            return output_path.read_text(encoding="utf-8", errors="ignore").strip()

        stdout = (completed.stdout or "").strip()
        if stdout:
            return stdout
        raise ValueError("OCR 未返回可用文本。")


def run_ocr(path: str | Path, settings: AppSettings) -> str:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(str(target))

    if (settings.umi_ocr_command or "").strip():
        return _run_command_ocr(target, settings)

    umi_executable = resolve_umi_ocr_path(settings)
    if umi_executable is None:
        raise ValueError("尚未配置 OCR。请在设置中选择或下载安装 Umi-OCR。")
    return _run_umi_doc_ocr(target, umi_executable, int(settings.umi_ocr_timeout_sec))


def extract_pdf_text_with_ocr(path: str | Path, pdf_text: str, settings: AppSettings) -> str:
    if pdf_text and len(pdf_text.strip()) >= 80:
        return pdf_text
    if not has_ocr_config(settings):
        return pdf_text
    try:
        ocr_text = run_ocr(path, settings)
    except Exception:
        return pdf_text
    if pdf_text.strip() and ocr_text.strip():
        return f"{pdf_text.strip()}\n\n{ocr_text.strip()}".strip()
    return ocr_text.strip() or pdf_text
