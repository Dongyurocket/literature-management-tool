from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import error, parse, request

from pypdf import PdfReader

from .utils import detect_note_format, extract_year, normalize_for_compare, sanitize_filename

CROSSREF_BASE = "https://api.crossref.org/works/"
OPENLIBRARY_BOOKS = "https://openlibrary.org/api/books"
HTTP_HEADERS = {
    "User-Agent": "LiteratureManagementTool/0.2 (local desktop app)",
    "Accept": "application/json",
}


def _get_json(url: str) -> dict:
    req = request.Request(url, headers=HTTP_HEADERS)
    with request.urlopen(req, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _authors_from_crossref(message: dict) -> list[str]:
    authors: list[str] = []
    for author in message.get("author", []):
        given = (author.get("given") or "").strip()
        family = (author.get("family") or "").strip()
        full = " ".join(part for part in (given, family) if part).strip()
        if full:
            authors.append(full)
    return authors


def _map_crossref_type(item_type: str) -> str:
    mapping = {
        "journal-article": "journal_article",
        "book": "book",
        "book-chapter": "book",
        "proceedings-article": "conference_paper",
        "dissertation": "thesis",
        "report": "report",
        "standard": "standard",
        "posted-content": "report",
    }
    return mapping.get(item_type, "misc")


def _payload_from_crossref(message: dict) -> dict:
    issued = message.get("issued", {}).get("date-parts", [[]])
    year = issued[0][0] if issued and issued[0] else None
    title = " ".join(message.get("title", [])).strip()
    subtitle = " ".join(message.get("subtitle", [])).strip()
    abstract = re.sub(r"<[^>]+>", " ", message.get("abstract", "") or "").strip()
    keywords = ", ".join(message.get("subject", []))
    container_title = " ".join(message.get("container-title", [])).strip()
    pages = message.get("page", "")
    doi = message.get("DOI", "")
    return {
        "entry_type": _map_crossref_type(message.get("type", "")),
        "title": title,
        "translated_title": subtitle,
        "publication_title": container_title,
        "publisher": message.get("publisher", ""),
        "conference_name": container_title if message.get("type") == "proceedings-article" else "",
        "year": year,
        "volume": message.get("volume", ""),
        "issue": message.get("issue", ""),
        "pages": pages,
        "doi": doi,
        "url": (message.get("URL") or ""),
        "language": message.get("language", ""),
        "keywords": keywords,
        "abstract": abstract,
        "summary": abstract[:240],
        "authors": _authors_from_crossref(message),
        "tags": [],
        "source_provider": "Crossref",
    }


def lookup_doi(doi: str) -> dict:
    normalized = (doi or "").strip()
    if not normalized:
        raise ValueError("缺少 DOI")
    normalized = normalized.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    url = CROSSREF_BASE + parse.quote(normalized)
    try:
        payload = _get_json(url)
    except error.HTTPError as exc:
        raise ValueError(f"DOI 查询失败：HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise ValueError(f"DOI 查询失败：{exc.reason}") from exc
    message = payload.get("message")
    if not isinstance(message, dict):
        raise ValueError("DOI 查询返回了无效数据")
    result = _payload_from_crossref(message)
    result["doi"] = normalized
    return result


def lookup_isbn(isbn: str) -> dict:
    normalized = re.sub(r"[^0-9Xx]", "", isbn or "")
    if not normalized:
        raise ValueError("缺少 ISBN")
    query = parse.urlencode({"bibkeys": f"ISBN:{normalized}", "format": "json", "jscmd": "data"})
    try:
        payload = _get_json(f"{OPENLIBRARY_BOOKS}?{query}")
    except error.URLError as exc:
        raise ValueError(f"ISBN 查询失败：{exc.reason}") from exc
    book = payload.get(f"ISBN:{normalized}")
    if not isinstance(book, dict):
        raise ValueError("没有找到对应的 ISBN 数据")
    title = book.get("title", "")
    subtitle = book.get("subtitle", "")
    publish_date = book.get("publish_date", "")
    publishers = ", ".join(item.get("name", "") for item in book.get("publishers", []))
    authors = [item.get("name", "") for item in book.get("authors", []) if item.get("name")]
    year = extract_year(publish_date)
    return {
        "entry_type": "book",
        "title": title,
        "translated_title": subtitle,
        "publisher": publishers,
        "year": year,
        "isbn": normalized,
        "url": book.get("url", ""),
        "authors": authors,
        "tags": [],
        "summary": (book.get("notes") or "")[:240],
        "source_provider": "OpenLibrary",
    }


def _parse_bib_fields(raw_fields: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    pattern = re.compile(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)", re.S)
    for key, value in pattern.findall(raw_fields):
        cleaned = value.strip().rstrip(",").strip()
        if cleaned.startswith("{") and cleaned.endswith("}"):
            cleaned = cleaned[1:-1]
        if cleaned.startswith('"') and cleaned.endswith('"'):
            cleaned = cleaned[1:-1]
        fields[key.lower()] = cleaned.strip()
    return fields


def parse_bib_text(content: str) -> list[dict]:
    entries: list[dict] = []
    parts = re.split(r"(?=@\w+\s*\{)", content or "", flags=re.I)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        header_match = re.match(r"@(\w+)\s*\{\s*([^,]+)\s*,", part, re.I)
        if not header_match:
            continue
        entry_type, cite_key = header_match.groups()
        body = part[header_match.end():].rstrip().rstrip("}")
        fields = _parse_bib_fields(body)
        authors = [item.strip() for item in re.split(r"\s+and\s+", fields.get("author", "")) if item.strip()]
        publication_title = fields.get("journal") or fields.get("booktitle", "")
        entries.append(
            {
                "entry_type": {
                    "article": "journal_article",
                    "book": "book",
                    "thesis": "thesis",
                    "inproceedings": "conference_paper",
                    "report": "report",
                    "misc": "misc",
                }.get(entry_type.lower(), "misc"),
                "cite_key": cite_key,
                "title": fields.get("title", ""),
                "translated_title": fields.get("titleaddon", ""),
                "publication_title": publication_title,
                "publisher": fields.get("publisher", ""),
                "school": fields.get("school", ""),
                "conference_name": fields.get("eventtitle", ""),
                "year": extract_year(fields.get("year", "")),
                "volume": fields.get("volume", ""),
                "issue": fields.get("number", ""),
                "pages": fields.get("pages", ""),
                "doi": fields.get("doi", ""),
                "isbn": fields.get("isbn", ""),
                "url": fields.get("url", ""),
                "language": fields.get("langid", ""),
                "subject": fields.get("usera", ""),
                "keywords": fields.get("keywords", ""),
                "summary": fields.get("annotation", ""),
                "abstract": fields.get("abstract", ""),
                "authors": authors,
                "tags": [],
                "source_provider": "BibTeX",
            }
        )
    return entries


def parse_ris_text(content: str) -> list[dict]:
    entries: list[dict] = []
    current: dict[str, list[str] | str] = {}
    for raw_line in (content or "").splitlines():
        line = raw_line.rstrip()
        match = re.match(r"^([A-Z0-9]{2})\s{2}-\s?(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if key == "TY":
            current = {"type": value, "authors": [], "keywords": []}
        elif key == "ER":
            entries.append(
                {
                    "entry_type": {
                        "JOUR": "journal_article",
                        "BOOK": "book",
                        "THES": "thesis",
                        "CONF": "conference_paper",
                        "RPRT": "report",
                    }.get(str(current.get("type", "")).upper(), "misc"),
                    "title": str(current.get("title", "")),
                    "translated_title": "",
                    "publication_title": str(current.get("publication_title", "")),
                    "publisher": str(current.get("publisher", "")),
                    "school": str(current.get("school", "")),
                    "conference_name": str(current.get("conference_name", "")),
                    "year": extract_year(str(current.get("year", ""))),
                    "volume": str(current.get("volume", "")),
                    "issue": str(current.get("issue", "")),
                    "pages": str(current.get("pages", "")),
                    "doi": str(current.get("doi", "")),
                    "isbn": str(current.get("isbn", "")),
                    "url": str(current.get("url", "")),
                    "language": str(current.get("language", "")),
                    "keywords": ", ".join(current.get("keywords", [])),
                    "summary": str(current.get("summary", "")),
                    "abstract": str(current.get("abstract", "")),
                    "authors": list(current.get("authors", [])),
                    "tags": [],
                    "source_provider": "RIS",
                }
            )
            current = {}
        elif key == "AU":
            current.setdefault("authors", []).append(value)
        elif key == "KW":
            current.setdefault("keywords", []).append(value)
        elif key in {"TI", "T1"}:
            current["title"] = value
        elif key in {"T2", "JO", "JF"}:
            current["publication_title"] = value
        elif key == "PB":
            current["publisher"] = value
        elif key == "CY":
            current["school"] = value
        elif key == "PY":
            current["year"] = value
        elif key == "VL":
            current["volume"] = value
        elif key == "IS":
            current["issue"] = value
        elif key == "SP":
            current["pages"] = value
        elif key == "EP":
            current["pages"] = f"{current.get('pages', '')}-{value}".strip("-")
        elif key == "DO":
            current["doi"] = value
        elif key == "SN":
            current["isbn"] = value
        elif key == "UR":
            current["url"] = value
        elif key == "LA":
            current["language"] = value
        elif key == "N1":
            current["summary"] = value
        elif key in {"N2", "AB"}:
            current["abstract"] = value
    return entries


def parse_reference_file(path: str | Path) -> list[dict]:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    if file_path.suffix.lower() == ".bib":
        return parse_bib_text(content)
    if file_path.suffix.lower() == ".ris":
        return parse_ris_text(content)
    raise ValueError("暂不支持该引用文件格式")


def infer_title_from_filename(path: str | Path) -> str:
    stem = Path(path).stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return sanitize_filename(stem, max_length=180)


def extract_pdf_text(path: str | Path, max_pages: int = 6) -> str:
    try:
        reader = PdfReader(str(path))
    except Exception:
        return ""
    chunks: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            chunks.append(text.strip())
    return "\n".join(chunks)


def infer_pdf_metadata(path: str | Path) -> dict:
    file_path = Path(path)
    title = ""
    authors: list[str] = []
    summary = ""
    year = extract_year(file_path.stem)
    doi = ""
    try:
        reader = PdfReader(str(file_path))
        meta = reader.metadata or {}
        title = (meta.get("/Title") or "").strip()
        author_text = (meta.get("/Author") or "").strip()
        if author_text:
            authors = [item.strip() for item in re.split(r"[,;/]", author_text) if item.strip()]
    except Exception:
        pass

    extracted_text = extract_pdf_text(file_path)
    if not title:
        title = infer_title_from_filename(file_path)
    if not year:
        year = extract_year(extracted_text[:1000])
    doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", extracted_text, flags=re.I)
    if doi_match:
        doi = doi_match.group(0)
    summary = " ".join(extracted_text.split())[:280]
    return {
        "entry_type": "journal_article",
        "title": title,
        "year": year,
        "authors": authors,
        "summary": summary,
        "abstract": summary,
        "doi": doi,
        "tags": [],
        "source_provider": "PDF",
    }


def scan_file(path: str | Path) -> list[dict]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in {".bib", ".ris"}:
        return [
            {
                "kind": "reference_record",
                "source_path": str(file_path),
                "payload": entry,
                "display_title": entry.get("title") or infer_title_from_filename(file_path),
                "role": "",
            }
            for entry in parse_reference_file(file_path)
        ]
    if suffix == ".pdf":
        payload = infer_pdf_metadata(file_path)
        return [
            {
                "kind": "file_record",
                "source_path": str(file_path),
                "payload": payload,
                "display_title": payload.get("title") or infer_title_from_filename(file_path),
                "role": "source",
            }
        ]
    if suffix in {".docx", ".md", ".markdown", ".txt"}:
        note_format = detect_note_format(file_path)
        return [
            {
                "kind": "note_record",
                "source_path": str(file_path),
                "payload": {
                    "entry_type": "misc",
                    "title": infer_title_from_filename(file_path),
                    "year": extract_year(file_path.stem),
                    "authors": [],
                    "tags": [],
                    "summary": "",
                    "note_format": note_format,
                },
                "display_title": infer_title_from_filename(file_path),
                "role": "note_file",
            }
        ]
    return []


def normalized_title_key(title: str) -> str:
    return normalize_for_compare(title)
