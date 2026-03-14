from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

ROLE_LABELS = {
    "source": "原文",
    "translation": "翻译",
    "note_file": "笔记",
    "supplement": "补充",
}

NOTE_FORMAT_LABELS = {
    "text": "文本",
    "markdown": "Markdown",
    "docx": "Word",
    "other": "其他",
}

ENTRY_TYPE_LABELS = {
    "journal_article": "期刊文章",
    "book": "图书",
    "thesis": "学位论文",
    "conference_paper": "会议论文",
    "standard": "标准",
    "patent": "专利",
    "report": "报告",
    "webpage": "网页",
    "misc": "其他",
}

ENTRY_TYPE_TO_BIB = {
    "journal_article": "article",
    "book": "book",
    "thesis": "thesis",
    "conference_paper": "inproceedings",
    "standard": "misc",
    "patent": "patent",
    "report": "report",
    "webpage": "online",
    "misc": "misc",
}


def now_text() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def sanitize_filename(value: str, max_length: int = 120) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]+", " ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "未命名"
    return cleaned[:max_length].strip(" .") or "未命名"


def split_multiline(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def split_csv(text: str) -> list[str]:
    parts = re.split(r"[,;；，]", text or "")
    return [part.strip() for part in parts if part.strip()]


def join_csv(values: Iterable[str]) -> str:
    return ", ".join(value for value in values if value)


def author_display(authors: list[str]) -> str:
    if not authors:
        return "佚名"
    first = sanitize_filename(authors[0], max_length=30)
    return f"{first}等" if len(authors) > 1 else first


def build_storage_name(authors: list[str], year: int | str | None, title: str) -> str:
    year_text = str(year) if year else "未知年份"
    return sanitize_filename(f"{author_display(authors)}_{year_text}_{title}", max_length=140)


def build_attachment_name(
    authors: list[str],
    year: int | str | None,
    title: str,
    role: str,
    suffix: str,
) -> str:
    label = ROLE_LABELS.get(role, "附件")
    ext = suffix if suffix.startswith(".") else (f".{suffix}" if suffix else "")
    base = sanitize_filename(f"{build_storage_name(authors, year, title)}_{label}", max_length=160)
    return f"{base}{ext.lower()}"


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def detect_note_format(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return "docx"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".txt", ".text"}:
        return "text"
    return "other"


def note_format_label(note_format: str) -> str:
    return NOTE_FORMAT_LABELS.get(note_format, NOTE_FORMAT_LABELS["other"])


def compute_checksum(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_docx_text(path: str | Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            data = archive.read("word/document.xml")
    except (FileNotFoundError, KeyError, OSError, zipfile.BadZipFile):
        return ""

    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ""

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        runs = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text = "".join(runs).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def load_note_preview(path: str | Path, max_length: int = 4000) -> str:
    note_path = Path(path)
    if not note_path.exists():
        return "笔记文件不存在。"

    note_format = detect_note_format(note_path)
    try:
        if note_format == "docx":
            content = read_docx_text(note_path)
        else:
            content = note_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = note_path.read_text(encoding="gbk")
        except (UnicodeDecodeError, OSError):
            content = ""
    except OSError:
        content = ""

    if not content.strip():
        return f"文件：{note_path}\n\n暂时无法预览该笔记内容。"

    content = content.strip()
    if len(content) > max_length:
        content = f"{content[:max_length].rstrip()}..."
    return f"文件：{note_path}\n格式：{note_format_label(note_format)}\n\n{content}"


def build_cite_key(authors: list[str], year: int | str | None, title: str) -> str:
    author = authors[0] if authors else "Anonymous"
    author_chunk = re.sub(r"\W+", "", author)[:12] or "Anonymous"
    year_chunk = str(year) if year else "n_d"
    title_chunk = re.sub(r"\W+", "", title)[:18] or "Untitled"
    return f"{author_chunk}{year_chunk}{title_chunk}"


def escape_bib_value(value: str) -> str:
    escaped = (value or "").replace("\\", "\\\\")
    escaped = escaped.replace("{", "\\{").replace("}", "\\}")
    return escaped.replace("\n", " ").strip()


def build_bib_entry(entry: dict) -> str:
    entry_type = ENTRY_TYPE_TO_BIB.get(entry.get("entry_type"), "misc")
    fields: list[tuple[str, str]] = []
    authors = entry.get("authors", [])
    if authors:
        fields.append(("author", " and ".join(authors)))
    if entry.get("title"):
        fields.append(("title", entry["title"]))
    if entry.get("translated_title"):
        fields.append(("titleaddon", entry["translated_title"]))
    if entry.get("year"):
        fields.append(("year", str(entry["year"])))
    if entry.get("publication_title"):
        key = "journal" if entry_type == "article" else "booktitle"
        fields.append((key, entry["publication_title"]))
    if entry.get("publisher"):
        fields.append(("publisher", entry["publisher"]))
    if entry.get("school"):
        fields.append(("school", entry["school"]))
    if entry.get("conference_name"):
        fields.append(("eventtitle", entry["conference_name"]))
    if entry.get("volume"):
        fields.append(("volume", entry["volume"]))
    if entry.get("issue"):
        fields.append(("number", entry["issue"]))
    if entry.get("pages"):
        fields.append(("pages", entry["pages"]))
    if entry.get("doi"):
        fields.append(("doi", entry["doi"]))
    if entry.get("isbn"):
        fields.append(("isbn", entry["isbn"]))
    if entry.get("url"):
        fields.append(("url", entry["url"]))
    if entry.get("language"):
        fields.append(("langid", entry["language"]))
    if entry.get("subject"):
        fields.append(("usera", entry["subject"]))
    if entry.get("keywords"):
        fields.append(("keywords", entry["keywords"]))
    if entry.get("summary"):
        fields.append(("annotation", entry["summary"]))
    if entry.get("abstract"):
        fields.append(("abstract", entry["abstract"]))
    if entry.get("standard_number"):
        fields.append(("note", f"标准号: {entry['standard_number']}"))
    if entry.get("patent_number"):
        fields.append(("number", entry["patent_number"]))

    cite_key = entry.get("cite_key") or build_cite_key(authors, entry.get("year"), entry.get("title", ""))
    lines = [f"@{entry_type}{{{cite_key},"]
    for key, value in fields:
        lines.append(f"  {key} = {{{escape_bib_value(value)}}},")
    lines.append("}")
    return "\n".join(lines)


def build_bibtex(entries: list[dict]) -> str:
    return "\n\n".join(build_bib_entry(entry) for entry in entries)
