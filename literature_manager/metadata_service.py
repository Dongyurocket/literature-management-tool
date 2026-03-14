from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import error, parse, request

from pypdf import PdfReader

from .config import AppSettings
from .ocr_service import extract_pdf_text_with_ocr
from .utils import detect_note_format, extract_year, normalize_for_compare, sanitize_filename

CROSSREF_WORK = "https://api.crossref.org/works/{doi}"
CROSSREF_SEARCH = "https://api.crossref.org/works?rows=1&query.bibliographic={query}"
DATACITE_DOI = "https://api.datacite.org/dois/{doi}"
OPENLIBRARY_BOOKS = "https://openlibrary.org/api/books"
OPENALEX_WORKS = "https://api.openalex.org/works?per-page=1&filter={query}"
OPENALEX_SEARCH = "https://api.openalex.org/works?per-page=1&search={query}"
GOOGLE_BOOKS = "https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}&maxResults=1"
HTTP_HEADERS = {
    "User-Agent": "LiteratureManagementTool/0.3.0",
    "Accept": "application/json",
}


def _get_json(url: str) -> dict:
    req = request.Request(url, headers=HTTP_HEADERS)
    with request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_get_json(url: str) -> dict:
    try:
        return _get_json(url)
    except error.HTTPError as exc:
        raise ValueError(f"HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise ValueError(str(exc.reason)) from exc


def _authors_from_crossref(message: dict) -> list[str]:
    authors: list[str] = []
    for author in message.get("author", []):
        given = (author.get("given") or "").strip()
        family = (author.get("family") or "").strip()
        full = " ".join(part for part in (given, family) if part).strip()
        if full:
            authors.append(full)
    return authors


def _authors_from_openalex(item: dict) -> list[str]:
    authors: list[str] = []
    for authorship in item.get("authorships", []):
        author = authorship.get("author", {})
        name = (author.get("display_name") or "").strip()
        if name:
            authors.append(name)
    return authors


def _authors_from_datacite(payload: dict) -> list[str]:
    authors: list[str] = []
    for creator in payload.get("creators", []):
        name = (creator.get("name") or "").strip()
        if name:
            authors.append(name)
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
        "pages": message.get("page", ""),
        "doi": message.get("DOI", ""),
        "url": message.get("URL", ""),
        "language": message.get("language", ""),
        "keywords": keywords,
        "abstract": abstract,
        "summary": abstract[:240],
        "authors": _authors_from_crossref(message),
        "tags": [],
        "source_provider": "Crossref",
    }


def _payload_from_datacite(attributes: dict) -> dict:
    titles = attributes.get("titles", [])
    title = titles[0].get("title", "") if titles else ""
    descriptions = attributes.get("descriptions", [])
    abstract = ""
    for item in descriptions:
        description = item.get("description")
        if description:
            abstract = str(description)
            break
    subjects: list[str] = []
    for item in attributes.get("subjects", []):
        if isinstance(item, dict) and item.get("subject"):
            subjects.append(str(item["subject"]))
        elif isinstance(item, str):
            subjects.append(item)
    return {
        "entry_type": _map_crossref_type(str(attributes.get("types", {}).get("resourceTypeGeneral", "")).lower()),
        "title": title,
        "translated_title": "",
        "publication_title": attributes.get("container", {}).get("title", ""),
        "publisher": attributes.get("publisher", ""),
        "year": extract_year(str(attributes.get("publicationYear", ""))),
        "doi": attributes.get("doi", ""),
        "url": attributes.get("url", ""),
        "language": attributes.get("language", ""),
        "keywords": ", ".join(subjects),
        "abstract": abstract,
        "summary": abstract[:240],
        "authors": _authors_from_datacite(attributes),
        "tags": [],
        "source_provider": "DataCite",
    }


def _payload_from_openalex(item: dict) -> dict:
    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    title = (item.get("display_name") or "").strip()
    abstract = ""
    if isinstance(item.get("abstract_inverted_index"), dict):
        pairs: list[tuple[int, str]] = []
        for word, positions in item["abstract_inverted_index"].items():
            for position in positions:
                pairs.append((int(position), word))
        pairs.sort(key=lambda pair: pair[0])
        abstract = " ".join(word for _position, word in pairs)
    return {
        "entry_type": _map_crossref_type(item.get("type", "")),
        "title": title,
        "translated_title": "",
        "publication_title": source.get("display_name", ""),
        "publisher": source.get("host_organization_name", ""),
        "conference_name": source.get("display_name", "") if item.get("type") == "proceedings-article" else "",
        "year": item.get("publication_year"),
        "volume": item.get("biblio", {}).get("volume", ""),
        "issue": item.get("biblio", {}).get("issue", ""),
        "pages": "-".join(
            part
            for part in [
                item.get("biblio", {}).get("first_page", ""),
                item.get("biblio", {}).get("last_page", ""),
            ]
            if part
        ),
        "doi": (item.get("doi") or "").replace("https://doi.org/", ""),
        "url": item.get("id", ""),
        "language": item.get("language", ""),
        "keywords": ", ".join(
            keyword.get("display_name", "")
            for keyword in item.get("keywords", [])
            if keyword.get("display_name")
        ),
        "abstract": abstract,
        "summary": abstract[:240],
        "authors": _authors_from_openalex(item),
        "tags": [],
        "source_provider": "OpenAlex",
    }


def _payload_from_openlibrary(book: dict, normalized: str) -> dict:
    title = book.get("title", "")
    subtitle = book.get("subtitle", "")
    publish_date = book.get("publish_date", "")
    publishers = ", ".join(item.get("name", "") for item in book.get("publishers", []))
    authors = [item.get("name", "") for item in book.get("authors", []) if item.get("name")]
    notes = book.get("notes")
    summary = notes if isinstance(notes, str) else ""
    return {
        "entry_type": "book",
        "title": title,
        "translated_title": subtitle,
        "publisher": publishers,
        "year": extract_year(publish_date),
        "isbn": normalized,
        "url": book.get("url", ""),
        "authors": authors,
        "tags": [],
        "summary": summary[:240],
        "source_provider": "OpenLibrary",
    }


def _payload_from_google_books(item: dict, normalized: str) -> dict:
    info = item.get("volumeInfo", {})
    industry_ids = info.get("industryIdentifiers", [])
    isbn = normalized
    for identifier in industry_ids:
        if str(identifier.get("type", "")).upper().startswith("ISBN"):
            isbn = identifier.get("identifier", normalized)
            break
    description = info.get("description", "")
    return {
        "entry_type": "book",
        "title": info.get("title", ""),
        "translated_title": info.get("subtitle", ""),
        "publisher": info.get("publisher", ""),
        "year": extract_year(info.get("publishedDate", "")),
        "isbn": isbn,
        "url": info.get("infoLink", ""),
        "authors": info.get("authors", []),
        "language": info.get("language", ""),
        "tags": [],
        "summary": description[:240],
        "abstract": description,
        "source_provider": "Google Books",
    }


def _lookup_doi_crossref(doi: str) -> dict:
    payload = _safe_get_json(CROSSREF_WORK.format(doi=parse.quote(doi)))
    message = payload.get("message")
    if not isinstance(message, dict):
        raise ValueError("Crossref 返回数据无效")
    result = _payload_from_crossref(message)
    result["doi"] = doi
    return result


def _lookup_doi_datacite(doi: str) -> dict:
    payload = _safe_get_json(DATACITE_DOI.format(doi=parse.quote(doi)))
    attributes = payload.get("data", {}).get("attributes")
    if not isinstance(attributes, dict):
        raise ValueError("DataCite 返回数据无效")
    result = _payload_from_datacite(attributes)
    result["doi"] = doi
    return result


def _lookup_doi_openalex(doi: str) -> dict:
    query = parse.quote(f"doi:https://doi.org/{doi}", safe=":/")
    payload = _safe_get_json(OPENALEX_WORKS.format(query=query))
    results = payload.get("results", [])
    if not results:
        raise ValueError("OpenAlex 未找到结果")
    result = _payload_from_openalex(results[0])
    result["doi"] = doi
    return result


def _lookup_isbn_openlibrary(isbn: str) -> dict:
    query = parse.urlencode({"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"})
    payload = _safe_get_json(f"{OPENLIBRARY_BOOKS}?{query}")
    book = payload.get(f"ISBN:{isbn}")
    if not isinstance(book, dict):
        raise ValueError("OpenLibrary 未找到结果")
    return _payload_from_openlibrary(book, isbn)


def _lookup_isbn_googlebooks(isbn: str) -> dict:
    payload = _safe_get_json(GOOGLE_BOOKS.format(isbn=parse.quote(isbn)))
    items = payload.get("items", [])
    if not items:
        raise ValueError("Google Books 未找到结果")
    return _payload_from_google_books(items[0], isbn)


def _lookup_title_openalex(title: str, authors: list[str], year: int | None) -> dict:
    query_parts = [title.strip()]
    if authors:
        query_parts.append(authors[0])
    if year:
        query_parts.append(str(year))
    payload = _safe_get_json(OPENALEX_SEARCH.format(query=parse.quote(" ".join(query_parts))))
    results = payload.get("results", [])
    if not results:
        raise ValueError("OpenAlex 未找到匹配结果")
    return _payload_from_openalex(results[0])


def _lookup_title_crossref(title: str, authors: list[str], year: int | None) -> dict:
    query_parts = [title.strip()]
    if authors:
        query_parts.append(authors[0])
    if year:
        query_parts.append(str(year))
    payload = _safe_get_json(CROSSREF_SEARCH.format(query=parse.quote(" ".join(query_parts))))
    items = payload.get("message", {}).get("items", [])
    if not items:
        raise ValueError("Crossref 未找到匹配结果")
    return _payload_from_crossref(items[0])


def _provider_chain(preferred_sources: list[str] | None, supported: list[str]) -> list[str]:
    if not preferred_sources:
        return supported
    ordered = [item for item in preferred_sources if item in supported]
    return ordered or supported


def lookup_doi(doi: str, preferred_sources: list[str] | None = None) -> dict:
    normalized = (doi or "").strip()
    if not normalized:
        raise ValueError("缺少 DOI。")
    normalized = normalized.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    providers = {
        "crossref": _lookup_doi_crossref,
        "datacite": _lookup_doi_datacite,
        "openalex": _lookup_doi_openalex,
    }
    errors: list[str] = []
    chain = _provider_chain(preferred_sources, list(providers))
    for provider_name in chain:
        try:
            payload = providers[provider_name](normalized)
            payload["metadata_fallback_chain"] = chain
            return payload
        except ValueError as exc:
            errors.append(f"{provider_name}: {exc}")
    raise ValueError("DOI 查询失败：" + "；".join(errors))


def lookup_isbn(isbn: str, preferred_sources: list[str] | None = None) -> dict:
    normalized = re.sub(r"[^0-9Xx]", "", isbn or "")
    if not normalized:
        raise ValueError("缺少 ISBN。")
    providers = {
        "openlibrary": _lookup_isbn_openlibrary,
        "googlebooks": _lookup_isbn_googlebooks,
    }
    errors: list[str] = []
    chain = _provider_chain(preferred_sources, list(providers))
    for provider_name in chain:
        try:
            payload = providers[provider_name](normalized)
            payload["metadata_fallback_chain"] = chain
            return payload
        except ValueError as exc:
            errors.append(f"{provider_name}: {exc}")
    raise ValueError("ISBN 查询失败：" + "；".join(errors))


def lookup_title_metadata(
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    preferred_sources: list[str] | None = None,
) -> dict:
    text = (title or "").strip()
    if not text:
        raise ValueError("缺少标题，无法执行标题回退查询。")
    providers = {
        "openalex": lambda: _lookup_title_openalex(text, authors or [], year),
        "crossref": lambda: _lookup_title_crossref(text, authors or [], year),
    }
    errors: list[str] = []
    chain = _provider_chain(preferred_sources, list(providers))
    for provider_name in chain:
        try:
            payload = providers[provider_name]()
            payload["metadata_fallback_chain"] = chain
            return payload
        except ValueError as exc:
            errors.append(f"{provider_name}: {exc}")
    raise ValueError("标题回退查询失败：" + "；".join(errors))


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
        body = part[header_match.end() :].rstrip().rstrip("}")
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
    raise ValueError("暂不支持该引文文件格式。")


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


def infer_pdf_metadata(path: str | Path, settings: AppSettings | None = None) -> dict:
    file_path = Path(path)
    title = ""
    authors: list[str] = []
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
    if settings is not None:
        extracted_text = extract_pdf_text_with_ocr(file_path, extracted_text, settings)
    if not title:
        title = infer_title_from_filename(file_path)
    if not year:
        year = extract_year(extracted_text[:1000])
    doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", extracted_text, flags=re.I)
    if doi_match:
        doi = doi_match.group(0)
    summary = " ".join(extracted_text.split())[:280]
    payload = {
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
    if settings is not None and extracted_text.strip():
        payload["ocr_text_preview"] = summary
    return payload


def scan_file(path: str | Path, settings: AppSettings | None = None) -> list[dict]:
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
        payload = infer_pdf_metadata(file_path, settings=settings)
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
