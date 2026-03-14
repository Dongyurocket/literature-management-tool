from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Iterable

from .utils import build_gbt_reference, join_csv


EXPORT_TEMPLATES = {
    "markdown_report": "Markdown 文献综述",
    "csv_catalog": "CSV 文献目录",
    "html_report": "HTML 阅读报告",
    "gbt_plaintext": "GB/T 参考文献纯文本",
}

REPORT_TEMPLATES = {
    "markdown_stats": "Markdown 统计报表",
    "json_stats": "JSON 统计报表",
}


def list_export_templates() -> dict[str, str]:
    return dict(EXPORT_TEMPLATES)


def list_report_templates() -> dict[str, str]:
    return dict(REPORT_TEMPLATES)


def suggested_extension(template_key: str) -> str:
    mapping = {
        "markdown_report": ".md",
        "csv_catalog": ".csv",
        "html_report": ".html",
        "gbt_plaintext": ".txt",
        "markdown_stats": ".md",
        "json_stats": ".json",
    }
    return mapping.get(template_key, ".txt")


def _reference_lines(literatures: Iterable[dict]) -> list[str]:
    return [reference for detail in literatures if (reference := build_gbt_reference(detail))]


def render_template(template_key: str, literatures: list[dict], *, library_name: str = "") -> str:
    if template_key == "gbt_plaintext":
        return "\n".join(_reference_lines(literatures))

    if template_key == "markdown_report":
        lines = [
            f"# {library_name or '文献导出报告'}",
            "",
            f"共导出 {len(literatures)} 条文献。",
            "",
        ]
        for index, detail in enumerate(literatures, start=1):
            authors = "、".join(detail.get("authors", [])) or "佚名"
            lines.extend(
                [
                    f"## {index}. {detail.get('title', '未命名文献')}",
                    "",
                    f"- 作者：{authors}",
                    f"- 年份：{detail.get('year') or '未标注'}",
                    f"- 类型：{detail.get('entry_type') or '未标注'}",
                    f"- 主题：{detail.get('subject') or '未分类'}",
                    f"- 关键词：{detail.get('keywords') or '无'}",
                    f"- 标签：{join_csv(detail.get('tags', [])) or '无'}",
                    f"- DOI：{detail.get('doi') or '无'}",
                    f"- ISBN：{detail.get('isbn') or '无'}",
                    "",
                    f"简介：{detail.get('summary') or '无'}",
                    "",
                    f"摘要：{detail.get('abstract') or '无'}",
                    "",
                ]
            )
        return "\n".join(lines).strip()

    if template_key == "html_report":
        cards: list[str] = []
        for detail in literatures:
            cards.append(
                "<section class='card'>"
                f"<h2>{html.escape(detail.get('title', '未命名文献'))}</h2>"
                f"<p><strong>作者：</strong>{html.escape('、'.join(detail.get('authors', [])) or '佚名')}</p>"
                f"<p><strong>年份：</strong>{html.escape(str(detail.get('year') or '未标注'))}</p>"
                f"<p><strong>主题：</strong>{html.escape(detail.get('subject') or '未分类')}</p>"
                f"<p><strong>关键词：</strong>{html.escape(detail.get('keywords') or '无')}</p>"
                f"<p><strong>简介：</strong>{html.escape(detail.get('summary') or '无')}</p>"
                "</section>"
            )
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(library_name or '文献导出报告')}</title>"
            "<style>body{font-family:'Microsoft YaHei',sans-serif;background:#f4f7fb;color:#17324d;padding:32px;}"
            "h1{margin-bottom:24px;} .card{background:#fff;border-radius:16px;padding:20px;margin-bottom:16px;"
            "box-shadow:0 10px 28px rgba(19,50,77,.08);} p{line-height:1.6;}</style>"
            "</head><body>"
            f"<h1>{html.escape(library_name or '文献导出报告')}</h1>"
            f"<p>共导出 {len(literatures)} 条文献。</p>"
            + "".join(cards)
            + "</body></html>"
        )

    raise ValueError("不支持的导出模板。")


def export_template_file(template_key: str, literatures: list[dict], destination: str, *, library_name: str = "") -> str:
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if template_key == "csv_catalog":
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["标题", "作者", "年份", "类型", "主题", "关键词", "简介", "DOI", "ISBN"])
            for detail in literatures:
                writer.writerow(
                    [
                        detail.get("title", ""),
                        " / ".join(detail.get("authors", [])),
                        detail.get("year", ""),
                        detail.get("entry_type", ""),
                        detail.get("subject", ""),
                        detail.get("keywords", ""),
                        detail.get("summary", ""),
                        detail.get("doi", ""),
                        detail.get("isbn", ""),
                    ]
                )
        return str(target)
    content = render_template(template_key, literatures, library_name=library_name)
    target.write_text(content, encoding="utf-8")
    return str(target)


def render_statistics_report(template_key: str, stats: dict, *, library_name: str = "") -> str:
    title = library_name or "文库统计报表"
    if template_key == "json_stats":
        return json.dumps({"library_name": title, **stats}, ensure_ascii=False, indent=2)
    if template_key != "markdown_stats":
        raise ValueError("不支持的统计报表模板。")

    def lines_for(items: list[dict], empty_label: str) -> list[str]:
        if not items:
            return [f"- {empty_label}"]
        return [f"- {item.get('label', '')}: {item.get('count', 0)}" for item in items]

    lines = [
        f"# {title}",
        "",
        f"- 文献总数：{stats.get('total_literatures', 0)}",
        f"- 附件总数：{stats.get('total_attachments', 0)}",
        f"- 笔记总数：{stats.get('total_notes', 0)}",
        "",
        "## 按年份",
        *lines_for(stats.get("by_year", []), "暂无数据"),
        "",
        "## 按主题",
        *lines_for(stats.get("by_subject", []), "暂无数据"),
        "",
        "## 按阅读状态",
        *lines_for(stats.get("by_status", []), "暂无数据"),
    ]
    return "\n".join(lines)


def export_statistics_report(template_key: str, stats: dict, destination: str, *, library_name: str = "") -> str:
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_statistics_report(template_key, stats, library_name=library_name), encoding="utf-8")
    return str(target)
