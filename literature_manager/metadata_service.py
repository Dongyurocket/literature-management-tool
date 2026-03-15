from __future__ import annotations

import html
import json
import re
import ssl
from pathlib import Path
from urllib import error, parse, request

try:
    import PIL
except ImportError:
    PIL = None
else:
    if not hasattr(PIL, "__version__"):
        PIL.__version__ = "unknown"

from pypdf import PdfReader

from . import __version__
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
USTC_OPENURL = "http://sfx.lib.ustc.edu.cn:3210/sfxlcl3/"
TSINGHUA_OPENURL = (
    "https://tsinghua-primo.hosted.exlibrisgroup.com/primo-explore/openurl"
    "?institution=86THU&vid=86THU"
)
CNKI_SEARCH = "https://kns.cnki.net/kns8s/defaultresult/index?{query}"
HTTP_HEADERS = {
    "User-Agent": f"LiteratureManagementTool/{__version__}",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
HTML_HEADERS = {
    **HTTP_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
CNKI_HEADERS = {
    **HTML_HEADERS,
    "Referer": "https://www.cnki.net/",
}


def _decode_response_text(body: bytes, charset: str | None = None) -> str:
    candidates = [charset, "utf-8", "gb18030", "big5"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="ignore")


def _get_text(url: str, headers: dict[str, str] | None = None) -> str:
    request_headers = headers or HTTP_HEADERS
    attempts: list[ssl.SSLContext | None] = [None]
    if url.lower().startswith("https://"):
        attempts.append(ssl._create_unverified_context())

    last_error: Exception | None = None
    for context in attempts:
        try:
            req = request.Request(url, headers=request_headers)
            with request.urlopen(req, timeout=15, context=context) as response:
                return _decode_response_text(
                    response.read(),
                    response.headers.get_content_charset(),
                )
        except error.HTTPError as exc:
            message = _decode_response_text(
                exc.read(),
                exc.headers.get_content_charset() if exc.headers else None,
            ).strip()
            detail = f"HTTP {exc.code}"
            if message:
                detail = f"{detail}: {message[:120]}"
            raise ValueError(detail) from exc
        except error.URLError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise ValueError(str(last_error.reason))
    raise ValueError("网络请求失败。")


def _get_json(url: str) -> dict:
    return json.loads(_get_text(url, headers=HTTP_HEADERS))


def _safe_get_json(url: str) -> dict:
    try:
        return _get_json(url)
    except json.JSONDecodeError as exc:
        raise ValueError("返回内容不是有效 JSON") from exc


def _safe_get_text(url: str, headers: dict[str, str] | None = None) -> str:
    return _get_text(url, headers=headers or HTML_HEADERS)


def _clean_html_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_html_attributes(fragment: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for key, raw_value in re.findall(r'([:\w-]+)\s*=\s*(".*?"|\'.*?\'|[^\s>]+)', fragment, re.S):
        value = raw_value.strip().strip('"').strip("'")
        attributes[key.lower()] = html.unescape(value)
    return attributes


def _collect_meta_tags(document: str) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    for fragment in re.findall(r"<meta\b([^>]+)>", document or "", flags=re.I):
        attributes = _parse_html_attributes(fragment)
        key = (
            attributes.get("name")
            or attributes.get("property")
            or attributes.get("itemprop")
            or attributes.get("http-equiv")
        )
        content = attributes.get("content")
        if not key or content is None:
            continue
        normalized_key = key.strip().lower()
        tags.setdefault(normalized_key, []).append(_clean_html_text(content))
    return tags


def _meta_values(meta_tags: dict[str, list[str]], *names: str) -> list[str]:
    values: list[str] = []
    for name in names:
        values.extend(item for item in meta_tags.get(name.lower(), []) if item)
    return values


def _first_non_empty(values: list[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def _normalize_doi_value(value: str) -> str:
    normalized = (value or "").strip()
    normalized = normalized.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return normalized.strip()


def _normalize_isbn_value(value: str) -> str:
    return re.sub(r"[^0-9Xx]", "", value or "")


def _normalize_author_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    authors: list[str] = []
    seen: set[str] = set()
    for raw_value in values or []:
        for item in re.split(r"[;\n]|(?:\s+and\s+)", raw_value or "", flags=re.I):
            candidate = item.strip().strip(",")
            if not candidate:
                continue
            key = normalize_for_compare(candidate)
            if key in seen:
                continue
            seen.add(key)
            authors.append(candidate)
    return authors


def _join_pages(first_page: str, last_page: str) -> str:
    first = (first_page or "").strip()
    last = (last_page or "").strip()
    if first and last:
        return f"{first}-{last}"
    return first or last


def _split_keywords(values: list[str]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        for item in re.split(r"[;,，；]", raw_value or ""):
            candidate = item.strip()
            if not candidate:
                continue
            key = normalize_for_compare(candidate)
            if key in seen:
                continue
            seen.add(key)
            parts.append(candidate)
    return ", ".join(parts)


def _merge_partial_payload(target: dict, source: dict) -> dict:
    merged = dict(target)
    for key, value in source.items():
        if value in ("", None, []):
            continue
        if key == "authors":
            existing = _normalize_author_list(merged.get("authors", []))
            incoming = _normalize_author_list(value)
            if len(incoming) > len(existing):
                merged["authors"] = incoming
            elif incoming and not existing:
                merged["authors"] = incoming
        elif key == "keywords":
            merged[key] = value if len(str(value)) > len(str(merged.get(key, ""))) else merged.get(key, "")
        elif key in {"abstract", "summary"}:
            merged[key] = value if len(str(value)) > len(str(merged.get(key, ""))) else merged.get(key, "")
        elif key == "entry_type":
            merged.setdefault(key, value)
        else:
            merged[key] = value if not merged.get(key) else merged.get(key)
    return merged


def _map_work_hint(item_type: str) -> str:
    normalized = (item_type or "").strip().lower()
    mapping = {
        "journalarticle": "journal_article",
        "article": "journal_article",
        "scholarlyarticle": "journal_article",
        "book": "book",
        "bookchapter": "book",
        "thesis": "thesis",
        "dissertation": "thesis",
        "conferencepaper": "conference_paper",
        "conferenceproceedings": "conference_paper",
        "report": "report",
    }
    return mapping.get(normalized, "")


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


def _authors_from_json_ld(value) -> list[str]:
    if isinstance(value, list):
        authors: list[str] = []
        for item in value:
            authors.extend(_authors_from_json_ld(item))
        return _normalize_author_list(authors)
    if isinstance(value, dict):
        if value.get("name"):
            return _normalize_author_list([str(value["name"])])
        given = str(value.get("givenName", "")).strip()
        family = str(value.get("familyName", "")).strip()
        full_name = " ".join(part for part in [given, family] if part).strip()
        return _normalize_author_list([full_name]) if full_name else []
    if isinstance(value, str):
        return _normalize_author_list([value])
    return []


def _flatten_json_ld_objects(value) -> list[dict]:
    objects: list[dict] = []
    if isinstance(value, dict):
        objects.append(value)
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                objects.extend(_flatten_json_ld_objects(item))
    elif isinstance(value, list):
        for item in value:
            objects.extend(_flatten_json_ld_objects(item))
    return [item for item in objects if isinstance(item, dict)]


def _payload_from_meta_tags(meta_tags: dict[str, list[str]], fallback_url: str, source_provider: str) -> dict:
    authors = _normalize_author_list(
        _meta_values(
            meta_tags,
            "citation_author",
            "dc.creator",
            "dcterms.creator",
            "author",
        )
    )
    publication_title = _first_non_empty(
        _meta_values(
            meta_tags,
            "citation_journal_title",
            "citation_conference_title",
            "citation_book_title",
            "prism.publicationname",
            "dc.source",
            "dcterms.ispartof",
        )
    )
    abstract = _first_non_empty(
        _meta_values(
            meta_tags,
            "citation_abstract",
            "description",
            "dc.description",
            "dcterms.abstract",
            "og:description",
        )
    )
    doi = _normalize_doi_value(
        _first_non_empty(
            _meta_values(
                meta_tags,
                "citation_doi",
                "dc.identifier",
                "dcterms.identifier",
                "prism.doi",
            )
        )
    )
    url = _first_non_empty(
        _meta_values(
            meta_tags,
            "citation_abstract_html_url",
            "citation_fulltext_html_url",
            "citation_public_url",
            "og:url",
        )
    ) or fallback_url
    entry_type = ""
    if _meta_values(meta_tags, "citation_book_title"):
        entry_type = "book"
    elif _meta_values(meta_tags, "citation_conference_title"):
        entry_type = "conference_paper"
    elif _meta_values(meta_tags, "citation_journal_title", "prism.publicationname"):
        entry_type = "journal_article"
    return {
        "entry_type": entry_type,
        "title": _first_non_empty(
            _meta_values(
                meta_tags,
                "citation_title",
                "dc.title",
                "dcterms.title",
                "og:title",
                "title",
            )
        ),
        "publication_title": publication_title,
        "publisher": _first_non_empty(_meta_values(meta_tags, "citation_publisher", "dc.publisher")),
        "conference_name": _first_non_empty(_meta_values(meta_tags, "citation_conference_title")),
        "year": extract_year(
            _first_non_empty(
                _meta_values(
                    meta_tags,
                    "citation_publication_date",
                    "citation_date",
                    "dc.date",
                    "dcterms.issued",
                    "prism.publicationdate",
                )
            )
        ),
        "volume": _first_non_empty(_meta_values(meta_tags, "citation_volume", "prism.volume")),
        "issue": _first_non_empty(_meta_values(meta_tags, "citation_issue", "prism.number")),
        "pages": _join_pages(
            _first_non_empty(_meta_values(meta_tags, "citation_firstpage")),
            _first_non_empty(_meta_values(meta_tags, "citation_lastpage")),
        )
        or _first_non_empty(_meta_values(meta_tags, "citation_pages")),
        "doi": doi,
        "isbn": _normalize_isbn_value(_first_non_empty(_meta_values(meta_tags, "citation_isbn", "isbn"))),
        "url": url,
        "language": _first_non_empty(_meta_values(meta_tags, "citation_language", "dc.language")),
        "keywords": _split_keywords(_meta_values(meta_tags, "citation_keywords", "keywords", "dc.subject")),
        "abstract": abstract,
        "summary": abstract[:240],
        "authors": authors,
        "source_provider": source_provider,
    }


def _payload_from_json_ld(document: str, fallback_url: str, source_provider: str) -> dict:
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        document or "",
        flags=re.I | re.S,
    )
    best: dict = {}
    for script in scripts:
        cleaned = script.strip()
        if not cleaned:
            continue
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        for obj in _flatten_json_ld_objects(data):
            type_hint = str(obj.get("@type", "")).replace(" ", "")
            payload = {
                "entry_type": _map_work_hint(type_hint),
                "title": _clean_html_text(str(obj.get("name") or obj.get("headline") or "")),
                "publication_title": "",
                "publisher": "",
                "year": extract_year(str(obj.get("datePublished") or obj.get("dateCreated") or "")),
                "doi": "",
                "isbn": "",
                "url": str(obj.get("url") or obj.get("@id") or fallback_url),
                "language": str(obj.get("inLanguage") or ""),
                "keywords": "",
                "abstract": _clean_html_text(str(obj.get("description") or obj.get("abstract") or "")),
                "summary": "",
                "authors": _authors_from_json_ld(obj.get("author")),
                "source_provider": source_provider,
            }
            if isinstance(obj.get("identifier"), list):
                identifiers = [str(item.get("value") if isinstance(item, dict) else item) for item in obj["identifier"]]
            else:
                raw_identifier = obj.get("identifier")
                identifiers = [str(raw_identifier)] if raw_identifier else []
            if not payload["doi"]:
                doi_match = next(
                    (
                        _normalize_doi_value(identifier)
                        for identifier in identifiers
                        if "10." in identifier or "doi" in identifier.lower()
                    ),
                    "",
                )
                payload["doi"] = doi_match
            if isinstance(obj.get("isPartOf"), dict):
                payload["publication_title"] = _clean_html_text(str(obj["isPartOf"].get("name") or ""))
            publisher = obj.get("publisher")
            if isinstance(publisher, dict):
                payload["publisher"] = _clean_html_text(str(publisher.get("name") or ""))
            keywords = obj.get("keywords")
            if isinstance(keywords, list):
                payload["keywords"] = _split_keywords([str(item) for item in keywords])
            elif isinstance(keywords, str):
                payload["keywords"] = _split_keywords([keywords])
            if payload["abstract"]:
                payload["summary"] = payload["abstract"][:240]
            best = _merge_partial_payload(best, payload)
    return best


def _sfx_scalar(document: str, key: str) -> str:
    match = re.search(rf"\|{re.escape(key)}\|\s*=>\s*\|(.*?)\|", document or "", flags=re.S)
    return _clean_html_text(match.group(1)) if match else ""


def _sfx_list(document: str, key: str) -> list[str]:
    match = re.search(rf"\|{re.escape(key)}\|\s*=>\s*\[(.*?)\]", document or "", flags=re.S)
    if not match:
        return []
    return _normalize_author_list(re.findall(r"\|(.*?)\|", match.group(1), flags=re.S))


def _payload_from_sfx_context(document: str, fallback_url: str, source_provider: str) -> dict:
    context_match = re.search(r"<ctx_object_1>(.*?)</ctx_object_1>", document or "", flags=re.S)
    if not context_match:
        return {}
    context = context_match.group(1)
    title = _sfx_scalar(context, "rft.atitle") or _sfx_scalar(context, "rft.btitle") or _sfx_scalar(context, "rft.title")
    publication_title = _sfx_scalar(context, "rft.jtitle")
    genre = _sfx_scalar(context, "rft.genre")
    pages = _join_pages(_sfx_scalar(context, "rft.spage"), _sfx_scalar(context, "rft.epage")) or _sfx_scalar(context, "rft.pages")
    doi = _normalize_doi_value(_sfx_scalar(context, "rft.doi"))
    isbn = _normalize_isbn_value(_sfx_scalar(context, "rft.isbn"))
    abstract = _sfx_scalar(context, "rft.description")
    return {
        "entry_type": _map_work_hint(genre),
        "title": title,
        "publication_title": publication_title,
        "publisher": _sfx_scalar(context, "rft.pub"),
        "conference_name": _sfx_scalar(context, "rft.conf_name"),
        "year": extract_year(_sfx_scalar(context, "rft.date") or _sfx_scalar(context, "rft.year")),
        "volume": _sfx_scalar(context, "rft.volume"),
        "issue": _sfx_scalar(context, "rft.issue"),
        "pages": pages,
        "doi": doi,
        "isbn": isbn,
        "url": fallback_url,
        "keywords": _sfx_scalar(context, "rft.subject"),
        "abstract": abstract,
        "summary": abstract[:240],
        "authors": _sfx_list(context, "@rft.au"),
        "source_provider": source_provider,
    }


def extract_partial_metadata_from_html(document: str, fallback_url: str, source_provider: str) -> dict:
    payload: dict = {}
    meta_tags = _collect_meta_tags(document)
    payload = _merge_partial_payload(payload, _payload_from_meta_tags(meta_tags, fallback_url, source_provider))
    payload = _merge_partial_payload(payload, _payload_from_json_ld(document, fallback_url, source_provider))
    payload = _merge_partial_payload(payload, _payload_from_sfx_context(document, fallback_url, source_provider))
    if not payload.get("title"):
        title_match = re.search(r"<title[^>]*>(.*?)</title>", document or "", flags=re.I | re.S)
        title_text = _clean_html_text(title_match.group(1)) if title_match else ""
        if title_text:
            payload["title"] = title_text
    if payload.get("abstract") and not payload.get("summary"):
        payload["summary"] = str(payload["abstract"])[:240]
    if payload:
        payload["source_provider"] = source_provider
        payload.setdefault("url", fallback_url)
    return payload


def _openurl_format_and_genre(entry_type: str | None = None, *, has_isbn: bool = False) -> tuple[str, str]:
    normalized = (entry_type or "").strip().lower()
    if has_isbn or normalized in {"book", "thesis"}:
        return "info:ofi/fmt:kev:mtx:book", "book"
    if normalized == "conference_paper":
        return "info:ofi/fmt:kev:mtx:journal", "conference"
    return "info:ofi/fmt:kev:mtx:journal", "article"


def _build_openurl_query(
    *,
    title: str = "",
    authors: list[str] | None = None,
    year: int | None = None,
    doi: str = "",
    isbn: str = "",
    entry_type: str | None = None,
) -> dict[str, str]:
    fmt, genre = _openurl_format_and_genre(entry_type, has_isbn=bool(isbn))
    query = {
        "url_ver": "Z39.88-2004",
        "ctx_ver": "Z39.88-2004",
        "rft_val_fmt": fmt,
        "genre": genre,
    }
    if doi:
        query["rft_id"] = f"info:doi/{doi}"
        query["rft.doi"] = doi
    if isbn:
        query["rft_id"] = f"urn:isbn:{isbn}"
        query["rft.isbn"] = isbn
    if title:
        if genre == "book":
            query["rft.btitle"] = title
        else:
            query["rft.atitle"] = title
            query["rft.title"] = title
    if authors:
        query["rft.au"] = authors[0]
    if year:
        query["rft.date"] = str(year)
    return query


def _build_url_with_query(base_url: str, query: dict[str, str]) -> str:
    parts = parse.urlsplit(base_url)
    existing = dict(parse.parse_qsl(parts.query, keep_blank_values=True))
    existing.update({key: value for key, value in query.items() if value})
    path = parts.path or "/"
    return parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path,
            parse.urlencode(existing, doseq=True),
            parts.fragment,
        )
    )


def _payload_has_useful_metadata(
    payload: dict,
    *,
    title: str = "",
    authors: list[str] | None = None,
    year: int | None = None,
    doi: str = "",
    isbn: str = "",
    lookup_url: str = "",
    provider_base_url: str = "",
) -> bool:
    if any(
        payload.get(key)
        for key in [
            "publication_title",
            "publisher",
            "conference_name",
            "volume",
            "issue",
            "pages",
            "keywords",
            "abstract",
            "summary",
            "language",
        ]
    ):
        return True

    resolved_url = str(payload.get("url", "") or "")
    if resolved_url and resolved_url not in {lookup_url, provider_base_url} and not resolved_url.startswith(provider_base_url):
        return True

    if payload.get("title") and normalize_for_compare(str(payload["title"])) != normalize_for_compare(title):
        return True
    if payload.get("year") and payload.get("year") != year:
        return True
    if payload.get("doi") and _normalize_doi_value(str(payload["doi"])) != _normalize_doi_value(doi):
        return True
    if payload.get("isbn") and _normalize_isbn_value(str(payload["isbn"])) != _normalize_isbn_value(isbn):
        return True

    incoming_authors = [normalize_for_compare(item) for item in _normalize_author_list(payload.get("authors", []))]
    existing_authors = [normalize_for_compare(item) for item in _normalize_author_list(authors or [])]
    return bool(incoming_authors and incoming_authors != existing_authors)


def _score_title_candidate(candidate: dict, title: str, authors: list[str], year: int | None) -> int:
    score = 0
    title_key = normalize_for_compare(title)
    candidate_title_key = normalize_for_compare(str(candidate.get("title", "")))
    if candidate_title_key == title_key and candidate_title_key:
        score += 100
    elif title_key and candidate_title_key and (title_key in candidate_title_key or candidate_title_key in title_key):
        score += 60
    if year and extract_year(str(candidate.get("year", ""))) == year:
        score += 15
    candidate_authors = normalize_for_compare(" ".join(candidate.get("authors", [])))
    for author in authors:
        author_key = normalize_for_compare(author)
        if author_key and author_key in candidate_authors:
            score += 10
            break
    return score


def _extract_cnki_search_candidates(document: str, search_url: str) -> list[dict]:
    candidates: list[dict] = []
    pattern = re.compile(
        r'<a\b([^>]*class=["\'][^"\']*\bfz14\b[^"\']*["\'][^>]*)>(.*?)</a>',
        flags=re.I | re.S,
    )
    for match in pattern.finditer(document or ""):
        attributes = _parse_html_attributes(match.group(1))
        href = attributes.get("href") or attributes.get("data-href") or attributes.get("data-url")
        title = _clean_html_text(match.group(2))
        if not href or not title:
            continue
        snippet = document[match.start() : match.start() + 1200]
        candidate = {
            "title": title,
            "url": parse.urljoin(search_url, href),
            "authors": _normalize_author_list(re.findall(r'author[^>]*>(.*?)<', snippet, flags=re.I | re.S)),
            "year": extract_year(snippet),
        }
        candidates.append(candidate)
    return candidates

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


def _lookup_openurl_metadata(
    base_url: str,
    source_provider: str,
    *,
    title: str = "",
    authors: list[str] | None = None,
    year: int | None = None,
    doi: str = "",
    isbn: str = "",
    entry_type: str | None = None,
) -> dict:
    lookup_url = _build_url_with_query(
        base_url,
        _build_openurl_query(
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            isbn=isbn,
            entry_type=entry_type,
        ),
    )
    document = _safe_get_text(lookup_url, headers=HTML_HEADERS)
    payload = extract_partial_metadata_from_html(document, lookup_url, source_provider)
    payload["metadata_search_url"] = lookup_url
    if not _payload_has_useful_metadata(
        payload,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        isbn=isbn,
        lookup_url=lookup_url,
        provider_base_url=base_url,
    ):
        raise ValueError("未返回可用元数据")
    return payload


def _lookup_cnki_metadata(
    *,
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    doi: str = "",
) -> dict:
    query_text = (doi or title).strip()
    if not query_text:
        raise ValueError("缺少可用于知网检索的标题或 DOI")
    search_url = CNKI_SEARCH.format(query=parse.urlencode({"kw": query_text}))
    search_document = _safe_get_text(search_url, headers=CNKI_HEADERS)
    candidates = _extract_cnki_search_candidates(search_document, search_url)
    if not candidates:
        raise ValueError("未找到匹配结果")

    best = max(candidates, key=lambda item: _score_title_candidate(item, title, authors or [], year))
    detail_url = str(best.get("url", "") or search_url)
    try:
        detail_document = _safe_get_text(detail_url, headers=CNKI_HEADERS)
    except ValueError:
        detail_document = search_document
        detail_url = search_url

    payload = extract_partial_metadata_from_html(detail_document, detail_url, "CNKI")
    payload = _merge_partial_payload(payload, best)
    payload["url"] = detail_url
    payload["source_provider"] = "CNKI"
    payload["metadata_search_url"] = search_url
    if not _payload_has_useful_metadata(
        payload,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        lookup_url=search_url,
        provider_base_url="https://kns.cnki.net/",
    ):
        raise ValueError("知网未返回可用元数据")
    return payload


def _lookup_doi_cnki(doi: str) -> dict:
    return _lookup_cnki_metadata(title="", authors=[], year=None, doi=doi)


def _lookup_doi_ustc_openurl(doi: str) -> dict:
    return _lookup_openurl_metadata(USTC_OPENURL, "USTC OpenURL", doi=doi)


def _lookup_doi_tsinghua_openurl(doi: str) -> dict:
    return _lookup_openurl_metadata(TSINGHUA_OPENURL, "Tsinghua OpenURL", doi=doi)


def _lookup_isbn_ustc_openurl(isbn: str) -> dict:
    return _lookup_openurl_metadata(
        USTC_OPENURL,
        "USTC OpenURL",
        isbn=isbn,
        entry_type="book",
    )


def _lookup_isbn_tsinghua_openurl(isbn: str) -> dict:
    return _lookup_openurl_metadata(
        TSINGHUA_OPENURL,
        "Tsinghua OpenURL",
        isbn=isbn,
        entry_type="book",
    )


def _lookup_title_cnki(title: str, authors: list[str], year: int | None, entry_type: str | None) -> dict:
    return _lookup_cnki_metadata(title=title, authors=authors, year=year)


def _lookup_title_ustc_openurl(
    title: str,
    authors: list[str],
    year: int | None,
    entry_type: str | None,
) -> dict:
    return _lookup_openurl_metadata(
        USTC_OPENURL,
        "USTC OpenURL",
        title=title,
        authors=authors,
        year=year,
        entry_type=entry_type,
    )


def _lookup_title_tsinghua_openurl(
    title: str,
    authors: list[str],
    year: int | None,
    entry_type: str | None,
) -> dict:
    return _lookup_openurl_metadata(
        TSINGHUA_OPENURL,
        "Tsinghua OpenURL",
        title=title,
        authors=authors,
        year=year,
        entry_type=entry_type,
    )


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


def _lookup_title_openalex(
    title: str,
    authors: list[str],
    year: int | None,
    entry_type: str | None = None,
) -> dict:
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


def _lookup_title_crossref(
    title: str,
    authors: list[str],
    year: int | None,
    entry_type: str | None = None,
) -> dict:
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
        "cnki": _lookup_doi_cnki,
        "ustc_openurl": _lookup_doi_ustc_openurl,
        "tsinghua_openurl": _lookup_doi_tsinghua_openurl,
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
        "ustc_openurl": _lookup_isbn_ustc_openurl,
        "tsinghua_openurl": _lookup_isbn_tsinghua_openurl,
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
    entry_type: str | None = None,
    preferred_sources: list[str] | None = None,
) -> dict:
    text = (title or "").strip()
    if not text:
        raise ValueError("缺少标题，无法执行标题回退查询。")
    providers = {
        "openalex": lambda: _lookup_title_openalex(text, authors or [], year, entry_type),
        "crossref": lambda: _lookup_title_crossref(text, authors or [], year, entry_type),
        "cnki": lambda: _lookup_title_cnki(text, authors or [], year, entry_type),
        "ustc_openurl": lambda: _lookup_title_ustc_openurl(text, authors or [], year, entry_type),
        "tsinghua_openurl": lambda: _lookup_title_tsinghua_openurl(text, authors or [], year, entry_type),
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
