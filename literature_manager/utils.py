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

IMPORT_MODE_LABELS = {
    "copy": "复制到库",
    "move": "移动到库",
    "link": "仅关联外部文件",
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

READING_STATUSES = ["未开始", "在读", "已读", "搁置"]


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


def load_note_content(path: str | Path) -> str:
    note_path = Path(path)
    note_format = detect_note_format(note_path)
    try:
        if note_format == "docx":
            return read_docx_text(note_path)
        return note_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return note_path.read_text(encoding="gbk")
        except (UnicodeDecodeError, OSError):
            return ""
    except OSError:
        return ""


def load_note_preview(path: str | Path, max_length: int = 4000) -> str:
    note_path = Path(path)
    if not note_path.exists():
        return "笔记文件不存在。"

    note_format = detect_note_format(note_path)
    content = load_note_content(note_path)

    if not content.strip():
        return f"文件：{note_path}\n\n暂时无法预览该笔记内容。"

    content = content.strip()
    if len(content) > max_length:
        content = f"{content[:max_length].rstrip()}..."
    return f"文件：{note_path}\n格式：{note_format_label(note_format)}\n\n{content}"


def normalize_for_compare(value: str) -> str:
    lowered = (value or "").lower()
    return re.sub(r"\W+", "", lowered)


def extract_year(text: str) -> int | None:
    match = re.search(r"(19|20)\d{2}", text or "")
    return int(match.group(0)) if match else None


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


def _split_person_field(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if re.search(r"\sand\s", text, flags=re.I):
        parts = re.split(r"\s+and\s+", text, flags=re.I)
        return [part.strip() for part in parts if part.strip()]
    return split_csv(text)


def _bib_people_text(value: str | list[str]) -> str:
    if isinstance(value, list):
        people = [item.strip() for item in value if str(item).strip()]
    else:
        people = _split_person_field(value)
    return " and ".join(people)


def _title_with_subtitle(entry: dict) -> str:
    title = str(entry.get("title", "") or "").strip()
    subtitle = str(entry.get("subtitle", "") or "").strip()
    translated_title = str(entry.get("translated_title", "") or "").strip()
    if subtitle:
        title = f"{title}：{subtitle}" if title else subtitle
    if translated_title:
        title = f"{title} / {translated_title}" if title else translated_title
    return title


def _date_text(year: int | str | None, month: str | int | None = "", day: str | int | None = "") -> str:
    if not year:
        return ""
    parts = [str(year)]
    month_text = str(month or "").strip()
    day_text = str(day or "").strip()
    if month_text:
        parts.append(month_text.zfill(2) if month_text.isdigit() else month_text)
    if day_text:
        parts.append(day_text.zfill(2) if day_text.isdigit() else day_text)
    return "-".join(parts)


def _parse_date_text(value: str) -> list[int] | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(?P<year>(?:19|20)\d{2})(?:[^\d]+(?P<month>\d{1,2}))?(?:[^\d]+(?P<day>\d{1,2}))?", text)
    if not match:
        year = extract_year(text)
        return [year] if year else None
    parts = [int(match.group("year"))]
    if match.group("month"):
        parts.append(int(match.group("month")))
    if match.group("day"):
        parts.append(int(match.group("day")))
    return parts


def _issued_date_parts(entry: dict) -> list[list[int]] | None:
    year = entry.get("year")
    if not year:
        return None
    parts = [int(year)]
    month = str(entry.get("month", "") or "").strip()
    day = str(entry.get("day", "") or "").strip()
    if month.isdigit():
        parts.append(int(month))
    if day.isdigit():
        if len(parts) == 1:
            parts.append(1)
        parts.append(int(day))
    return [parts]


def _people_to_csl(values: list[str]) -> list[dict]:
    items: list[dict] = []
    for name in values:
        parts = [part for part in str(name).split() if part]
        if len(parts) >= 2:
            items.append({"family": parts[-1], "given": " ".join(parts[:-1])})
        else:
            items.append({"literal": str(name)})
    return items


def build_bib_entry(entry: dict) -> str:
    entry_kind = str(entry.get("entry_type", "misc") or "misc")
    entry_type = ENTRY_TYPE_TO_BIB.get(entry_kind, "misc")
    fields: list[tuple[str, str]] = []
    authors = entry.get("authors", [])
    if authors:
        fields.append(("author", " and ".join(authors)))
    editors = _bib_people_text(str(entry.get("editors", "") or ""))
    if editors:
        fields.append(("editor", editors))
    translators = _bib_people_text(str(entry.get("translators", "") or ""))
    if translators:
        fields.append(("translator", translators))
    if entry.get("title"):
        fields.append(("title", entry["title"]))
    if entry.get("subtitle"):
        fields.append(("subtitle", entry["subtitle"]))
    if entry.get("translated_title"):
        fields.append(("titleaddon", entry["translated_title"]))
    if entry.get("year"):
        fields.append(("year", str(entry["year"])))
    if entry.get("month"):
        fields.append(("month", str(entry["month"])))
    if entry.get("day"):
        fields.append(("day", str(entry["day"])))
    if entry.get("publication_title"):
        key = "journal" if entry_type == "article" else "booktitle"
        fields.append((key, entry["publication_title"]))
    if entry.get("publisher"):
        fields.append(("publisher", entry["publisher"]))
    if entry.get("publication_place"):
        fields.append(("location", entry["publication_place"]))
    if entry.get("school"):
        fields.append(("school", entry["school"]))
    if entry.get("institution"):
        fields.append(("institution", entry["institution"]))
    if entry.get("conference_name"):
        fields.append(("eventtitle", entry["conference_name"]))
    if entry.get("conference_place"):
        fields.append(("venue", entry["conference_place"]))
    if entry.get("degree"):
        fields.append(("type", entry["degree"]))
    if entry.get("edition"):
        fields.append(("edition", entry["edition"]))
    if entry.get("volume"):
        fields.append(("volume", entry["volume"]))
    if entry_kind == "journal_article" and entry.get("issue"):
        fields.append(("number", entry["issue"]))
    elif entry_kind == "standard" and entry.get("standard_number"):
        fields.append(("number", entry["standard_number"]))
    elif entry_kind == "patent" and entry.get("patent_number"):
        fields.append(("number", entry["patent_number"]))
    elif entry_kind == "report" and entry.get("report_number"):
        fields.append(("number", entry["report_number"]))
    if entry.get("pages"):
        fields.append(("pages", entry["pages"]))
    if entry.get("doi"):
        fields.append(("doi", entry["doi"]))
    if entry.get("isbn"):
        fields.append(("isbn", entry["isbn"]))
    if entry.get("url"):
        fields.append(("url", entry["url"]))
    if entry.get("access_date"):
        fields.append(("urldate", entry["access_date"]))
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
    if entry.get("remarks"):
        fields.append(("note", entry["remarks"]))

    cite_key = entry.get("cite_key") or build_cite_key(authors, entry.get("year"), entry.get("title", ""))
    lines = [f"@{entry_type}{{{cite_key},"]
    for key, value in fields:
        lines.append(f"  {key} = {{{escape_bib_value(value)}}},")
    lines.append("}")
    return "\n".join(lines)


def build_bibtex(entries: list[dict]) -> str:
    return "\n\n".join(build_bib_entry(entry) for entry in entries)


def build_gbt_reference(entry: dict) -> str:
    authors = entry.get("authors", [])
    editors = _split_person_field(str(entry.get("editors", "") or ""))
    translators = _split_person_field(str(entry.get("translators", "") or ""))
    author_text = "，".join(authors or editors) if (authors or editors) else "佚名"
    title = _title_with_subtitle(entry)
    year = entry.get("year", "")
    month = entry.get("month", "")
    day = entry.get("day", "")
    date_text = _date_text(year, month, day)
    publication_title = entry.get("publication_title", "")
    publisher = entry.get("publisher", "") or entry.get("school", "") or entry.get("institution", "")
    publication_place = entry.get("publication_place", "")
    pages = entry.get("pages", "")
    volume = entry.get("volume", "")
    issue = entry.get("issue", "")
    doi = entry.get("doi", "")
    url = entry.get("url", "")
    access_date = entry.get("access_date", "")
    entry_type = entry.get("entry_type", "misc")
    edition = entry.get("edition", "")
    conference_name = entry.get("conference_name", "") or publication_title
    conference_place = entry.get("conference_place", "")
    institution = entry.get("institution", "")
    degree = entry.get("degree", "")
    standard_number = entry.get("standard_number", "")
    patent_number = entry.get("patent_number", "")
    report_number = entry.get("report_number", "")

    if entry_type == "journal_article":
        parts = [f"{author_text}. {title}[J]"]
        if publication_title:
            parts.append(publication_title)
        detail = []
        if date_text:
            detail.append(date_text)
        if volume:
            volume_part = str(volume)
            if issue:
                volume_part += f"({issue})"
            detail.append(volume_part)
        elif issue:
            detail.append(f"({issue})")
        if pages:
            detail.append(str(pages))
        if detail:
            parts.append(", ".join(detail))
        reference = ". ".join(part for part in parts if part)
    elif entry_type == "book":
        detail = []
        if translators:
            detail.append("，".join(translators))
        if edition:
            detail.append(f"{edition}版" if not str(edition).endswith("版") else str(edition))
        publish_info = ""
        if publication_place and publisher:
            publish_info = f"{publication_place}: {publisher}"
        else:
            publish_info = publication_place or publisher
        if date_text:
            publish_info = ", ".join(part for part in [publish_info, date_text] if part)
        detail_text = ". ".join(part for part in detail if part)
        reference = ". ".join(part for part in [f"{author_text}. {title}[M]", detail_text, publish_info] if part)
    elif entry_type == "thesis":
        school = entry.get("school", "") or publisher
        publish_info = ""
        if publication_place and school:
            publish_info = f"{publication_place}: {school}"
        else:
            publish_info = publication_place or school
        if date_text:
            publish_info = ", ".join(part for part in [publish_info, date_text] if part)
        if degree:
            publish_info = ". ".join(part for part in [publish_info, degree] if part)
        reference = ". ".join(part for part in [f"{author_text}. {title}[D]", publish_info] if part)
    elif entry_type == "conference_paper":
        container = conference_name or publisher
        publish_info = ""
        if conference_place and publisher:
            publish_info = f"{conference_place}: {publisher}"
        else:
            publish_info = conference_place or publisher
        if date_text:
            publish_info = ", ".join(part for part in [publish_info, date_text] if part)
        if pages:
            publish_info = ", ".join(part for part in [publish_info, str(pages)] if part)
        reference = ". ".join(part for part in [f"{author_text}. {title}[C]", container, publish_info] if part)
    elif entry_type == "report":
        report_body = report_number or institution
        publish_info = ""
        if publication_place and publisher:
            publish_info = f"{publication_place}: {publisher}"
        else:
            publish_info = publication_place or publisher
        if date_text:
            publish_info = ", ".join(part for part in [publish_info, date_text] if part)
        reference = ". ".join(part for part in [f"{author_text}. {title}[R]", report_body, publish_info] if part)
    elif entry_type == "standard":
        publish_info = ""
        if publication_place and publisher:
            publish_info = f"{publication_place}: {publisher}"
        else:
            publish_info = publication_place or publisher
        if date_text:
            publish_info = ", ".join(part for part in [publish_info, date_text] if part)
        standard_head = " ".join(part for part in [standard_number, title] if part).strip()
        reference = ". ".join(part for part in [f"{standard_head}[S]", publish_info] if part)
    elif entry_type == "patent":
        owner_text = "，".join(authors) if authors else (institution or "佚名")
        patent_info = " ".join(part for part in [entry.get("country", ""), patent_number] if part).strip()
        if date_text:
            patent_info = ", ".join(part for part in [patent_info, date_text] if part)
        reference = ". ".join(part for part in [f"{owner_text}. {title}[P]", patent_info] if part)
    elif entry_type == "webpage":
        container = publication_title or publisher or institution
        publish_info = ", ".join(part for part in [container, date_text] if part)
        if access_date:
            publish_info = f"{publish_info}[{access_date}]" if publish_info else f"[{access_date}]"
        reference = ". ".join(part for part in [f"{author_text}. {title}[EB/OL]", publish_info, url] if part)
    else:
        publish_info = ""
        if publication_place and publisher:
            publish_info = f"{publication_place}: {publisher}"
        else:
            publish_info = publication_place or publisher
        if date_text:
            publish_info = ", ".join(part for part in [publish_info, date_text] if part)
        if publication_title:
            publish_info = ". ".join(part for part in [publication_title, publish_info] if part)
        reference = ". ".join(part for part in [f"{author_text}. {title}[Z]", publish_info] if part)
    if doi:
        reference = f"{reference}. DOI:{doi}"
    return reference.strip(" ,.")


def build_csl_entry(entry: dict) -> dict:
    authors = entry.get("authors", [])
    author_items = _people_to_csl(authors)
    editor_items = _people_to_csl(_split_person_field(str(entry.get("editors", "") or "")))
    translator_items = _people_to_csl(_split_person_field(str(entry.get("translators", "") or "")))
    csl_type = {
        "journal_article": "article-journal",
        "book": "book",
        "thesis": "thesis",
        "conference_paper": "paper-conference",
        "report": "report",
        "standard": "standard",
        "patent": "patent",
        "webpage": "webpage",
    }.get(entry.get("entry_type", "misc"), "article")
    issued = _issued_date_parts(entry)
    accessed_parts = _parse_date_text(str(entry.get("access_date", "") or ""))
    special_number = (
        entry.get("standard_number")
        or entry.get("patent_number")
        or entry.get("report_number")
        or ""
    )
    payload = {
        "id": entry.get("cite_key") or build_cite_key(authors, entry.get("year"), entry.get("title", "")),
        "type": csl_type,
        "title": _title_with_subtitle(entry),
        "author": author_items,
        "editor": editor_items,
        "translator": translator_items,
        "issued": {"date-parts": issued} if issued else None,
        "accessed": {"date-parts": [accessed_parts]} if accessed_parts else None,
        "container-title": entry.get("publication_title", ""),
        "publisher": entry.get("publisher", "") or entry.get("school", "") or entry.get("institution", ""),
        "publisher-place": entry.get("publication_place", ""),
        "event": entry.get("conference_name", ""),
        "event-place": entry.get("conference_place", ""),
        "genre": entry.get("degree", ""),
        "edition": entry.get("edition", ""),
        "number": special_number,
        "volume": entry.get("volume", ""),
        "issue": entry.get("issue", ""),
        "page": entry.get("pages", ""),
        "DOI": entry.get("doi", ""),
        "ISBN": entry.get("isbn", ""),
        "URL": entry.get("url", ""),
        "language": entry.get("language", ""),
        "abstract": entry.get("abstract", ""),
        "keyword": entry.get("keywords", ""),
        "note": entry.get("remarks", ""),
    }
    return {key: value for key, value in payload.items() if value not in ("", None, [], {})}
