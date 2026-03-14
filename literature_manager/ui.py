
from __future__ import annotations

import json
import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from .config import AppSettings, SettingsStore
from .db import LibraryDatabase
from .dedupe_service import find_duplicate_groups, merge_literatures
from .import_service import import_scanned_items, scan_import_sources
from .maintenance_service import create_backup, find_missing_paths, repair_missing_paths, restore_backup
from .metadata_service import lookup_doi, lookup_isbn
from .utils import (
    ENTRY_TYPE_LABELS,
    ROLE_LABELS,
    build_csl_entry,
    build_cite_key,
    build_gbt_reference,
    detect_note_format,
    join_csv,
    load_note_preview,
    note_format_label,
    split_csv,
    split_multiline,
)

ENTRY_TYPE_OPTIONS = list(ENTRY_TYPE_LABELS.items())
ENTRY_TYPE_BY_LABEL = {label: code for code, label in ENTRY_TYPE_OPTIONS}
ROLE_OPTIONS = list(ROLE_LABELS.items())
ROLE_BY_LABEL = {label: code for code, label in ROLE_OPTIONS}
IMPORT_MODE_LABELS = {
    "copy": "复制到库",
    "move": "移动到库",
    "link": "仅关联外部文件",
}
IMPORT_MODE_BY_LABEL = {label: code for code, label in IMPORT_MODE_LABELS.items()}
READING_STATUSES = ["未开始", "在读", "已读", "搁置"]


def entry_type_label(code: str) -> str:
    return ENTRY_TYPE_LABELS.get(code, code or "")


def role_label(code: str) -> str:
    return ROLE_LABELS.get(code, code or "")


def note_type_label(note_type: str) -> str:
    return "文件笔记" if note_type == "file" else "文本笔记"


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
    else:
        subprocess.Popen(["xdg-open", str(target)])


def reveal_path(path: str) -> None:
    target = Path(path)
    if os.name == "nt":
        if target.exists() and target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["explorer", str(target.parent if target.suffix else target)])
    else:
        open_path(str(target.parent))


class BaseDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, geometry: str) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry(geometry)
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.bind("<Escape>", lambda _event: self.destroy())


class LiteratureDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, literature: dict | None = None) -> None:
        super().__init__(parent, "文献信息", "920x760")
        self.literature = literature or {}
        self.vars = {
            "entry_type": tk.StringVar(value=entry_type_label(self.literature.get("entry_type", "journal_article"))),
            "title": tk.StringVar(value=self.literature.get("title", "")),
            "translated_title": tk.StringVar(value=self.literature.get("translated_title", "")),
            "year": tk.StringVar(value=str(self.literature.get("year", "") or "")),
            "month": tk.StringVar(value=self.literature.get("month", "")),
            "subject": tk.StringVar(value=self.literature.get("subject", "")),
            "keywords": tk.StringVar(value=self.literature.get("keywords", "")),
            "tags": tk.StringVar(value=join_csv(self.literature.get("tags", []))),
            "reading_status": tk.StringVar(value=self.literature.get("reading_status", READING_STATUSES[0])),
            "rating": tk.StringVar(value=str(self.literature.get("rating", "") or "")),
            "publication_title": tk.StringVar(value=self.literature.get("publication_title", "")),
            "publisher": tk.StringVar(value=self.literature.get("publisher", "")),
            "school": tk.StringVar(value=self.literature.get("school", "")),
            "conference_name": tk.StringVar(value=self.literature.get("conference_name", "")),
            "standard_number": tk.StringVar(value=self.literature.get("standard_number", "")),
            "patent_number": tk.StringVar(value=self.literature.get("patent_number", "")),
            "volume": tk.StringVar(value=self.literature.get("volume", "")),
            "issue": tk.StringVar(value=self.literature.get("issue", "")),
            "pages": tk.StringVar(value=self.literature.get("pages", "")),
            "doi": tk.StringVar(value=self.literature.get("doi", "")),
            "isbn": tk.StringVar(value=self.literature.get("isbn", "")),
            "url": tk.StringVar(value=self.literature.get("url", "")),
            "language": tk.StringVar(value=self.literature.get("language", "")),
            "country": tk.StringVar(value=self.literature.get("country", "")),
            "cite_key": tk.StringVar(value=self.literature.get("cite_key", "")),
        }
        self._build_ui()
        self.wait_visibility()
        self.focus_set()

    def _add_entry(
        self,
        frame: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        column: int = 0,
        width: int = 38,
    ) -> ttk.Entry:
        ttk.Label(frame, text=label).grid(row=row, column=column * 2, sticky="w", padx=8, pady=6)
        entry = ttk.Entry(frame, textvariable=variable, width=width)
        entry.grid(row=row, column=column * 2 + 1, sticky="ew", padx=8, pady=6)
        return entry

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.grid(sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(container)
        notebook.grid(row=0, column=0, sticky="nsew")

        basic = ttk.Frame(notebook, padding=12)
        publication = ttk.Frame(notebook, padding=12)
        extra = ttk.Frame(notebook, padding=12)
        notebook.add(basic, text="基本信息")
        notebook.add(publication, text="出版信息")
        notebook.add(extra, text="扩展信息")

        for frame in (basic, publication, extra):
            frame.columnconfigure(1, weight=1)
            frame.columnconfigure(3, weight=1)

        ttk.Label(basic, text="文献类型").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        type_combo = ttk.Combobox(
            basic,
            textvariable=self.vars["entry_type"],
            values=[label for _, label in ENTRY_TYPE_OPTIONS],
            state="readonly",
            width=36,
        )
        type_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        self._add_entry(basic, 0, "年份", self.vars["year"], column=1)
        self._add_entry(basic, 1, "标题", self.vars["title"], width=90)
        self._add_entry(basic, 2, "译名/副标题", self.vars["translated_title"], width=90)
        self._add_entry(basic, 3, "月份", self.vars["month"])
        self._add_entry(basic, 3, "主题", self.vars["subject"], column=1)
        self._add_entry(basic, 4, "关键词", self.vars["keywords"], width=90)
        self._add_entry(basic, 5, "标签", self.vars["tags"], width=90)
        ttk.Label(basic, text="阅读状态").grid(row=6, column=0, sticky="w", padx=8, pady=6)
        status_combo = ttk.Combobox(
            basic,
            textvariable=self.vars["reading_status"],
            values=READING_STATUSES,
            state="readonly",
            width=36,
        )
        status_combo.grid(row=6, column=1, sticky="ew", padx=8, pady=6)
        self._add_entry(basic, 6, "评分", self.vars["rating"], column=1)

        ttk.Label(basic, text="作者（每行一位）").grid(row=7, column=0, sticky="nw", padx=8, pady=6)
        self.author_text = scrolledtext.ScrolledText(basic, height=7, width=40, wrap="word")
        self.author_text.grid(row=7, column=1, columnspan=3, sticky="nsew", padx=8, pady=6)
        self.author_text.insert("1.0", "\n".join(self.literature.get("authors", [])))

        ttk.Label(basic, text="一句话简介").grid(row=8, column=0, sticky="nw", padx=8, pady=6)
        self.summary_text = scrolledtext.ScrolledText(basic, height=5, width=40, wrap="word")
        self.summary_text.grid(row=8, column=1, columnspan=3, sticky="nsew", padx=8, pady=6)
        self.summary_text.insert("1.0", self.literature.get("summary", ""))
        self._add_entry(publication, 0, "期刊/书名", self.vars["publication_title"], width=90)
        self._add_entry(publication, 1, "出版社", self.vars["publisher"], width=90)
        self._add_entry(publication, 2, "学校/机构", self.vars["school"], width=90)
        self._add_entry(publication, 3, "会议名称", self.vars["conference_name"], width=90)
        self._add_entry(publication, 4, "标准号", self.vars["standard_number"])
        self._add_entry(publication, 4, "专利号", self.vars["patent_number"], column=1)
        self._add_entry(publication, 5, "卷", self.vars["volume"])
        self._add_entry(publication, 5, "期", self.vars["issue"], column=1)
        self._add_entry(publication, 6, "页码", self.vars["pages"])
        self._add_entry(publication, 6, "语种", self.vars["language"], column=1)
        self._add_entry(publication, 7, "DOI", self.vars["doi"], width=90)
        self._add_entry(publication, 8, "ISBN", self.vars["isbn"], width=90)
        self._add_entry(publication, 9, "URL", self.vars["url"], width=90)
        self._add_entry(publication, 10, "国家/地区", self.vars["country"], width=90)

        self._add_entry(extra, 0, "引用键", self.vars["cite_key"], width=70)
        ttk.Button(extra, text="自动生成", command=self._generate_cite_key).grid(row=0, column=2, sticky="w", padx=8, pady=6)

        ttk.Label(extra, text="摘要").grid(row=1, column=0, sticky="nw", padx=8, pady=6)
        self.abstract_text = scrolledtext.ScrolledText(extra, height=10, wrap="word")
        self.abstract_text.grid(row=1, column=1, columnspan=3, sticky="nsew", padx=8, pady=6)
        self.abstract_text.insert("1.0", self.literature.get("abstract", ""))

        ttk.Label(extra, text="备注").grid(row=2, column=0, sticky="nw", padx=8, pady=6)
        self.remarks_text = scrolledtext.ScrolledText(extra, height=8, wrap="word")
        self.remarks_text.grid(row=2, column=1, columnspan=3, sticky="nsew", padx=8, pady=6)
        self.remarks_text.insert("1.0", self.literature.get("remarks", ""))

        button_row = ttk.Frame(container)
        button_row.grid(row=1, column=0, sticky="e", pady=(10, 0))
        ttk.Button(button_row, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(button_row, text="保存", command=self._submit).pack(side="right")

    def _generate_cite_key(self) -> None:
        authors = split_multiline(self.author_text.get("1.0", "end"))
        year = self.vars["year"].get().strip()
        title = self.vars["title"].get().strip()
        self.vars["cite_key"].set(build_cite_key(authors, year, title))

    def _submit(self) -> None:
        title = self.vars["title"].get().strip()
        if not title:
            messagebox.showerror("缺少标题", "请至少填写文献标题。", parent=self)
            return

        year_text = self.vars["year"].get().strip()
        rating_text = self.vars["rating"].get().strip()
        year = None
        rating = None
        if year_text:
            try:
                year = int(year_text)
            except ValueError:
                messagebox.showerror("年份格式错误", "年份请填写整数。", parent=self)
                return
        if rating_text:
            try:
                rating = int(rating_text)
            except ValueError:
                messagebox.showerror("评分格式错误", "评分请填写整数。", parent=self)
                return

        self.result = {
            "id": self.literature.get("id"),
            "entry_type": ENTRY_TYPE_BY_LABEL.get(self.vars["entry_type"].get(), "journal_article"),
            "title": title,
            "translated_title": self.vars["translated_title"].get().strip(),
            "year": year,
            "month": self.vars["month"].get().strip(),
            "subject": self.vars["subject"].get().strip(),
            "keywords": self.vars["keywords"].get().strip(),
            "reading_status": self.vars["reading_status"].get().strip(),
            "rating": rating,
            "publication_title": self.vars["publication_title"].get().strip(),
            "publisher": self.vars["publisher"].get().strip(),
            "school": self.vars["school"].get().strip(),
            "conference_name": self.vars["conference_name"].get().strip(),
            "standard_number": self.vars["standard_number"].get().strip(),
            "patent_number": self.vars["patent_number"].get().strip(),
            "volume": self.vars["volume"].get().strip(),
            "issue": self.vars["issue"].get().strip(),
            "pages": self.vars["pages"].get().strip(),
            "doi": self.vars["doi"].get().strip(),
            "isbn": self.vars["isbn"].get().strip(),
            "url": self.vars["url"].get().strip(),
            "language": self.vars["language"].get().strip(),
            "country": self.vars["country"].get().strip(),
            "summary": self.summary_text.get("1.0", "end").strip(),
            "abstract": self.abstract_text.get("1.0", "end").strip(),
            "remarks": self.remarks_text.get("1.0", "end").strip(),
            "cite_key": self.vars["cite_key"].get().strip(),
            "authors": split_multiline(self.author_text.get("1.0", "end")),
            "tags": split_csv(self.vars["tags"].get()),
        }
        self.destroy()


class AttachmentImportDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, settings: AppSettings, file_count: int) -> None:
        super().__init__(parent, "导入附件", "480x280")
        self.vars = {
            "role": tk.StringVar(value=role_label("source")),
            "language": tk.StringVar(value=""),
            "import_mode": tk.StringVar(value=IMPORT_MODE_LABELS.get(settings.default_import_mode, "复制到库")),
            "is_primary": tk.BooleanVar(value=True),
        }
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=f"已选择 {file_count} 个文件").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(frame, text="文件角色").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.vars["role"], values=[label for _, label in ROLE_OPTIONS], state="readonly").grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="语言").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.vars["language"]).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="导入方式").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.vars["import_mode"], values=list(IMPORT_MODE_LABELS.values()), state="readonly").grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(frame, text="设为该角色主文件", variable=self.vars["is_primary"]).grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        button_row = ttk.Frame(frame)
        button_row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(button_row, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(button_row, text="导入", command=self._submit).pack(side="right")

    def _submit(self) -> None:
        self.result = {
            "role": ROLE_BY_LABEL[self.vars["role"].get()],
            "language": self.vars["language"].get().strip(),
            "import_mode": IMPORT_MODE_BY_LABEL[self.vars["import_mode"].get()],
            "is_primary": bool(self.vars["is_primary"].get()),
        }
        self.destroy()


class NoteDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, attachments: list[dict], note: dict | None = None) -> None:
        super().__init__(parent, "笔记", "700x560")
        self.attachments = attachments
        note = note or {}
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        self.title_var = tk.StringVar(value=note.get("title", ""))
        ttk.Label(frame, text="标题").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.title_var).grid(row=1, column=0, sticky="ew", pady=(4, 12))

        ttk.Label(frame, text="关联附件（可多选）").grid(row=2, column=0, sticky="w")
        list_frame = ttk.Frame(frame)
        list_frame.grid(row=3, column=0, sticky="nsew", pady=(4, 12))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.attachment_list = tk.Listbox(list_frame, selectmode="multiple", exportselection=False, height=6)
        self.attachment_list.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.attachment_list.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.attachment_list.config(yscrollcommand=list_scroll.set)
        linked_ids = set(note.get("attachment_ids", []))
        for index, attachment in enumerate(attachments):
            self.attachment_list.insert("end", f"{role_label(attachment['role'])} | {attachment['label']}")
            if attachment["id"] in linked_ids:
                self.attachment_list.selection_set(index)

        ttk.Label(frame, text="内容").grid(row=4, column=0, sticky="w")
        self.content_text = scrolledtext.ScrolledText(frame, wrap="word")
        self.content_text.grid(row=5, column=0, sticky="nsew", pady=(4, 12))
        self.content_text.insert("1.0", note.get("content", ""))
        frame.rowconfigure(5, weight=1)

        button_row = ttk.Frame(frame)
        button_row.grid(row=6, column=0, sticky="e")
        ttk.Button(button_row, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(button_row, text="保存", command=self._submit).pack(side="right")
        self.note_id = note.get("id")

    def _submit(self) -> None:
        title = self.title_var.get().strip() or "未命名笔记"
        selected_indexes = self.attachment_list.curselection()
        attachment_ids = [self.attachments[index]["id"] for index in selected_indexes]
        self.result = {
            "id": self.note_id,
            "title": title,
            "content": self.content_text.get("1.0", "end").strip(),
            "attachment_ids": attachment_ids,
            "note_type": "text",
        }
        self.destroy()


class NoteFileDialog(BaseDialog):
    def __init__(
        self,
        parent: tk.Misc,
        settings: AppSettings,
        attachments: list[dict],
        note: dict | None = None,
    ) -> None:
        super().__init__(parent, "关联笔记文件", "720x520")
        self.attachments = attachments
        self.settings = settings
        note = note or {}
        self.note_id = note.get("id")
        self.title_var = tk.StringVar(value=note.get("title", ""))
        self.file_var = tk.StringVar(value=note.get("resolved_path", ""))
        self.import_mode_var = tk.StringVar(value=IMPORT_MODE_LABELS.get(settings.default_import_mode, "复制到库"))

        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="标题").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.title_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(frame, text="笔记文件").grid(row=1, column=0, sticky="w", pady=6)
        state = "readonly" if self.note_id else "normal"
        ttk.Entry(frame, textvariable=self.file_var, state=state).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="浏览", command=self._browse, state="disabled" if self.note_id else "normal").grid(
            row=1,
            column=2,
            padx=(8, 0),
            pady=6,
        )

        ttk.Label(frame, text="导入方式").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(
            frame,
            textvariable=self.import_mode_var,
            values=list(IMPORT_MODE_LABELS.values()),
            state="readonly" if not self.note_id else "disabled",
        ).grid(row=2, column=1, sticky="w", pady=6)

        ttk.Label(frame, text="关联附件（可多选）").grid(row=3, column=0, sticky="w", pady=(10, 6))
        self.attachment_list = tk.Listbox(frame, selectmode="multiple", exportselection=False, height=8)
        self.attachment_list.grid(row=4, column=0, columnspan=3, sticky="nsew")
        linked_ids = set(note.get("attachment_ids", []))
        for index, attachment in enumerate(attachments):
            self.attachment_list.insert("end", f"{role_label(attachment['role'])} | {attachment['label']}")
            if attachment["id"] in linked_ids:
                self.attachment_list.selection_set(index)

        button_row = ttk.Frame(frame)
        button_row.grid(row=5, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(button_row, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(button_row, text="保存", command=self._submit).pack(side="right")

    def _browse(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="选择笔记文件",
            filetypes=[
                ("支持的笔记文件", "*.docx *.md *.markdown *.txt"),
                ("Word 文档", "*.docx"),
                ("Markdown", "*.md *.markdown"),
                ("文本文件", "*.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if selected:
            self.file_var.set(selected)
            if not self.title_var.get().strip():
                self.title_var.set(Path(selected).stem)

    def _submit(self) -> None:
        file_path = self.file_var.get().strip()
        if not file_path:
            messagebox.showerror("缺少文件", "请选择要关联的笔记文件。", parent=self)
            return
        selected_indexes = self.attachment_list.curselection()
        attachment_ids = [self.attachments[index]["id"] for index in selected_indexes]
        self.result = {
            "id": self.note_id,
            "title": self.title_var.get().strip() or Path(file_path).stem,
            "content": "",
            "attachment_ids": attachment_ids,
            "note_type": "file",
            "note_format": detect_note_format(file_path),
            "external_file_path": "" if self.note_id else file_path,
            "import_mode": IMPORT_MODE_BY_LABEL[self.import_mode_var.get()],
        }
        self.destroy()


class SettingsDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, settings: AppSettings) -> None:
        super().__init__(parent, "软件设置", "760x340")
        self.original_settings = settings
        self.library_root_var = tk.StringVar(value=settings.library_root)
        self.import_mode_var = tk.StringVar(value=IMPORT_MODE_LABELS.get(settings.default_import_mode, "复制到库"))
        self.pdf_reader_var = tk.StringVar(value=settings.pdf_reader_path)
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="文献库目录").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.library_root_var).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="浏览", command=self._browse).grid(row=0, column=2, padx=(8, 0), pady=6)

        ttk.Label(frame, text="默认导入方式").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.import_mode_var, values=list(IMPORT_MODE_LABELS.values()), state="readonly").grid(row=1, column=1, sticky="w", pady=6)

        ttk.Label(frame, text="PDF 阅读器").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.pdf_reader_var).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="浏览", command=self._browse_pdf_reader).grid(row=2, column=2, padx=(8, 0), pady=6)

        tips = (
            "说明：\n"
            "1. 文献库目录用于保存复制/移动导入的 PDF、翻译稿和补充材料。\n"
            "2. 仅关联外部文件时，不会改动原始文件位置。\n"
            "3. 配置 PDF 阅读器后，打开 PDF 会优先使用该软件。"
        )
        ttk.Label(frame, text=tips, justify="left").grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))

        button_row = ttk.Frame(frame)
        button_row.grid(row=4, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(button_row, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(button_row, text="保存", command=self._submit).pack(side="right")

    def _browse(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="选择文献库目录")
        if selected:
            self.library_root_var.set(selected)

    def _browse_pdf_reader(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="选择 PDF 阅读器",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if selected:
            self.pdf_reader_var.set(selected)

    def _submit(self) -> None:
        self.result = AppSettings(
            library_root=self.library_root_var.get().strip(),
            default_import_mode=IMPORT_MODE_BY_LABEL[self.import_mode_var.get()],
            recent_export_dir=self.original_settings.recent_export_dir,
            pdf_reader_path=self.pdf_reader_var.get().strip(),
        )
        self.destroy()


class RenamePreviewDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, previews: list[dict]) -> None:
        super().__init__(parent, "PDF 重命名预览", "980x560")
        self.previews = previews
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(frame, columns=("old", "new", "status"), show="headings", height=18)
        for key, text, width in (("old", "原路径", 320), ("new", "新路径", 360), ("status", "状态", 80)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.config(yscrollcommand=scroll.set)
        for item in previews:
            status = "待执行" if item.get("changed") else "无变化"
            self.tree.insert("", "end", values=(item["old_path"], item["new_path"], status))

        button_row = ttk.Frame(frame)
        button_row.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(button_row, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(button_row, text="执行重命名", command=self._submit).pack(side="right")

    def _submit(self) -> None:
        self.result = True
        self.destroy()


class MetadataPreviewDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, payload: dict) -> None:
        super().__init__(parent, "元数据预览", "760x520")
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = scrolledtext.ScrolledText(frame, wrap="word")
        text.grid(row=0, column=0, sticky="nsew")
        preview_lines = [
            f"标题：{payload.get('title', '')}",
            f"作者：{' / '.join(payload.get('authors', []))}",
            f"年份：{payload.get('year', '')}",
            f"来源：{payload.get('publication_title', '')}",
            f"出版社：{payload.get('publisher', '')}",
            f"DOI：{payload.get('doi', '')}",
            f"ISBN：{payload.get('isbn', '')}",
            f"URL：{payload.get('url', '')}",
            f"关键词：{payload.get('keywords', '')}",
            "",
            "摘要 / 简介：",
            payload.get("abstract") or payload.get("summary", ""),
        ]
        text.insert("1.0", "\n".join(preview_lines).strip())
        text.configure(state="disabled")
        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(buttons, text="用缺失字段补全", command=self._submit).pack(side="right")

    def _submit(self) -> None:
        self.result = True
        self.destroy()


class ImportCenterDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, settings: AppSettings) -> None:
        super().__init__(parent, "导入中心", "1100x620")
        self.settings = settings
        self.items: list[dict] = []
        self.import_mode_var = tk.StringVar(value=IMPORT_MODE_LABELS.get(settings.default_import_mode, "复制到库"))
        self.status_var = tk.StringVar(value="请选择文件或文件夹进行扫描。")

        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="选择文件", command=self._pick_files).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="选择文件夹", command=self._pick_folder).pack(side="left", padx=(0, 12))
        ttk.Label(toolbar, text="导入方式").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.import_mode_var, values=list(IMPORT_MODE_LABELS.values()), state="readonly", width=12).pack(side="left", padx=(8, 0))

        self.tree = ttk.Treeview(
            frame,
            columns=("kind", "title", "type", "authors", "year", "source"),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("kind", "来源类型", 90),
            ("title", "标题", 260),
            ("type", "文献类型", 110),
            ("authors", "作者", 180),
            ("year", "年份", 70),
            ("source", "源文件", 320),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.config(yscrollcommand=scroll.set)

        bottom = ttk.Frame(frame)
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(bottom, text="导入选中项", command=self._submit).pack(side="right")

    def _load_sources(self, paths: list[str]) -> None:
        self.items = scan_import_sources(paths)
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.items):
            kind_label = {"reference_record": "Bib/RIS", "file_record": "PDF", "note_record": "笔记文件"}.get(item["kind"], item["kind"])
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    kind_label,
                    item["display_title"],
                    entry_type_label(item.get("entry_type", "")),
                    " / ".join(item.get("authors", [])),
                    item.get("year") or "",
                    item["source_path"],
                ),
            )
        self.tree.selection_set(self.tree.get_children())
        self.status_var.set(f"已扫描 {len(self.items)} 个可导入项。")

    def _pick_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self,
            title="选择要导入的文件",
            filetypes=[
                ("支持的文件", "*.pdf *.bib *.ris *.docx *.md *.markdown *.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if files:
            self._load_sources(list(files))

    def _pick_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="选择要扫描的文件夹")
        if folder:
            self._load_sources([folder])

    def _submit(self) -> None:
        selected_indexes = {int(item_id) for item_id in self.tree.selection()}
        for index, item in enumerate(self.items):
            item["selected"] = index in selected_indexes
        self.result = {
            "items": self.items,
            "import_mode": IMPORT_MODE_BY_LABEL[self.import_mode_var.get()],
        }
        self.destroy()


class DuplicateDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, groups: list[dict]) -> None:
        super().__init__(parent, "重复文献检测", "980x600")
        self.groups = groups
        self.current_group: dict | None = None
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        ttk.Label(left, text="重复组").pack(anchor="w")
        self.group_list = tk.Listbox(left, exportselection=False, width=30, height=22)
        self.group_list.pack(fill="both", expand=True, pady=(6, 0))
        self.group_list.bind("<<ListboxSelect>>", self._on_group_selected)
        for group in groups:
            self.group_list.insert("end", f"{group['reason']} | {group['items'][0]['title']} 等 {len(group['items'])} 条")

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        ttk.Label(right, text="选择一条作为保留主记录，再合并其余记录").grid(row=0, column=0, sticky="w")
        self.tree = ttk.Treeview(right, columns=("id", "title", "year", "authors"), show="headings", selectmode="browse")
        for key, text, width in (("id", "ID", 50), ("title", "标题", 320), ("year", "年份", 70), ("authors", "作者", 220)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

        buttons = ttk.Frame(right)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(buttons, text="合并到当前选中", command=self._submit).pack(side="right")

        if groups:
            self.group_list.selection_set(0)
            self._on_group_selected()

    def _on_group_selected(self, _event=None) -> None:
        selection = self.group_list.curselection()
        if not selection:
            return
        self.current_group = self.groups[selection[0]]
        self.tree.delete(*self.tree.get_children())
        for item in self.current_group["items"]:
            self.tree.insert("", "end", iid=str(item["id"]), values=(item["id"], item["title"], item.get("year") or "", " / ".join(item.get("authors", []))))
        if self.current_group["items"]:
            self.tree.selection_set(str(self.current_group["items"][0]["id"]))

    def _submit(self) -> None:
        if not self.current_group:
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一条要保留的记录。", parent=self)
            return
        primary_id = int(selection[0])
        merged_ids = [int(item["id"]) for item in self.current_group["items"] if int(item["id"]) != primary_id]
        self.result = {"primary_id": primary_id, "merged_ids": merged_ids, "reason": self.current_group["reason"]}
        self.destroy()


class SearchDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, database: LibraryDatabase) -> None:
        super().__init__(parent, "全文搜索", "980x600")
        self.database = database
        self.query_var = tk.StringVar()
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Entry(toolbar, textvariable=self.query_var).pack(side="left", fill="x", expand=True)
        ttk.Button(toolbar, text="搜索", command=self._search).pack(side="left", padx=(8, 0))

        self.tree = ttk.Treeview(frame, columns=("title", "year", "authors", "hit"), show="headings")
        for key, text, width in (("title", "标题", 300), ("year", "年份", 70), ("authors", "作者", 180), ("hit", "命中片段", 340)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _event: self._submit())

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(buttons, text="定位到选中文献", command=self._submit).pack(side="right")

    def _search(self) -> None:
        rows = self.database.search_literatures(self.query_var.get().strip())
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            hit = row.get("summary_hit") or row.get("notes_hit") or row.get("attachment_hit") or ""
            self.tree.insert("", "end", iid=str(row["id"]), values=(row.get("title", ""), row.get("year") or "", row.get("authors_display", ""), hit))

    def _submit(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self.result = int(selection[0])
        self.destroy()


class MaintenanceDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, database: LibraryDatabase, settings_store: SettingsStore, settings: AppSettings) -> None:
        super().__init__(parent, "维护工具", "980x620")
        self.database = database
        self.settings_store = settings_store
        self.settings = settings
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="刷新缺失文件", command=self._load_missing).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="按目录修复", command=self._repair).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="重建搜索索引", command=self._rebuild_index).pack(side="left", padx=(0, 18))
        ttk.Button(toolbar, text="创建备份", command=self._backup).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="恢复备份", command=self._restore).pack(side="left")

        self.tree = ttk.Treeview(frame, columns=("kind", "name", "path"), show="headings")
        for key, text, width in (("kind", "类型", 80), ("name", "名称", 220), ("path", "缺失路径", 520)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.config(yscrollcommand=scroll.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")

        self._load_missing()

    def _load_missing(self) -> None:
        rows = find_missing_paths(self.database)
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(rows):
            kind = "笔记" if item.get("kind") == "note" else "附件"
            name = item.get("title") or item.get("label") or f"ID {item['id']}"
            self.tree.insert("", "end", iid=str(index), values=(kind, name, item.get("resolved_path", "")))

    def _repair(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="选择用于修复的搜索目录")
        if not folder:
            return
        try:
            result = repair_missing_paths(self.database, folder)
        except ValueError as exc:
            messagebox.showerror("修复失败", str(exc), parent=self)
            return
        self._load_missing()
        messagebox.showinfo("修复完成", f"已修复 {result['fixed']} 条，仍有 {result['unresolved']} 条未解决。", parent=self)

    def _rebuild_index(self) -> None:
        self.database.rebuild_search_index()
        messagebox.showinfo("完成", "全文搜索索引已重建。", parent=self)

    def _backup(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="创建备份",
            defaultextension=".zip",
            filetypes=[("ZIP 文件", "*.zip")],
            initialfile="literature_manager_backup.zip",
        )
        if not path:
            return
        backup_path = create_backup(self.settings_store, self.settings, path)
        messagebox.showinfo("备份完成", f"已创建备份：\n{backup_path}", parent=self)

    def _restore(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="选择备份文件", filetypes=[("ZIP 文件", "*.zip")])
        if not path:
            return
        if not messagebox.askyesno("确认恢复", "恢复备份将覆盖当前元数据。建议先手动备份一次。继续吗？", parent=self):
            return
        self.result = {"restore_path": path}
        self.destroy()


class StatisticsDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, database: LibraryDatabase) -> None:
        super().__init__(parent, "统计面板", "760x620")
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = scrolledtext.ScrolledText(frame, wrap="word")
        text.grid(row=0, column=0, sticky="nsew")
        stats = database.get_statistics()
        sections = [
            f"文献总数：{stats['total_literatures']}",
            f"附件总数：{stats['total_attachments']}",
            f"笔记总数：{stats['total_notes']}",
            "",
            "按年份统计：",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_year"]) or "- 无数据",
            "",
            "按主题统计：",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_subject"]) or "- 无数据",
            "",
            "按阅读状态统计：",
            "\n".join(f"- {item['label']}: {item['count']}" for item in stats["by_status"]) or "- 无数据",
        ]
        text.insert("1.0", "\n".join(sections))
        text.configure(state="disabled")
        ttk.Button(frame, text="关闭", command=self.destroy).grid(row=1, column=0, sticky="e", pady=(10, 0))


class MainWindow(ttk.Frame):
    def __init__(self, parent: tk.Misc, database: LibraryDatabase, settings_store: SettingsStore, settings: AppSettings) -> None:
        super().__init__(parent)
        self.parent = parent
        self.db = database
        self.settings_store = settings_store
        self.settings = settings
        self.current_literature_id: int | None = None
        self.current_detail: dict | None = None
        self.search_var = tk.StringVar()
        self.subject_var = tk.StringVar()
        self.year_var = tk.StringVar()
        self.type_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")
        self._build_ui()
        self.refresh_filters()
        self.refresh_literatures()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.grid(row=0, column=0, sticky="ew")
        for index, (text, command) in enumerate(
            [
                ("新建文献", self.new_literature),
                ("编辑", self.edit_literature),
                ("删除", self.delete_literature),
                ("导入中心", self.open_import_center),
                ("元数据补全", self.fill_metadata),
                ("添加附件", self.add_attachments),
                ("新增笔记", self.add_note),
                ("导出 Bib", self.export_bib),
                ("复制国标", self.copy_gbt_reference),
                ("导出 CSL", self.export_csl_json),
                ("PDF 重命名", self.rename_pdfs),
                ("查重", self.open_dedupe_center),
                ("全文搜索", self.open_search_center),
                ("维护", self.open_maintenance_tools),
                ("统计", self.open_statistics),
                ("设置", self.open_settings),
            ]
        ):
            ttk.Button(toolbar, text=text, command=command).grid(row=0, column=index, padx=(0, 8))

        filter_bar = ttk.Frame(self, padding=(12, 0, 12, 10))
        filter_bar.grid(row=1, column=0, sticky="ew")
        filter_bar.columnconfigure(1, weight=1)
        ttk.Label(filter_bar, text="搜索").grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew", padx=(8, 12))
        search_entry.bind("<Return>", lambda _event: self.refresh_literatures())
        ttk.Label(filter_bar, text="类型").grid(row=0, column=2, sticky="w")
        self.type_combo = ttk.Combobox(filter_bar, textvariable=self.type_var, state="readonly", width=14)
        self.type_combo.grid(row=0, column=3, padx=(8, 12))
        self.type_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_literatures())
        ttk.Label(filter_bar, text="年份").grid(row=0, column=4, sticky="w")
        self.year_combo = ttk.Combobox(filter_bar, textvariable=self.year_var, state="readonly", width=10)
        self.year_combo.grid(row=0, column=5, padx=(8, 12))
        self.year_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_literatures())
        ttk.Button(filter_bar, text="查询", command=self.refresh_literatures).grid(row=0, column=6, padx=(0, 8))
        ttk.Button(filter_bar, text="清空筛选", command=self.clear_filters).grid(row=0, column=7)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.grid(row=2, column=0, sticky="nsew")

        left = ttk.Frame(body, padding=10)
        center = ttk.Frame(body, padding=(0, 10, 10, 10))
        right = ttk.Frame(body, padding=(0, 10, 10, 10))
        body.add(left, weight=1)
        body.add(center, weight=4)
        body.add(right, weight=3)

        self._build_left_panel(left)
        self._build_center_panel(center)
        self._build_right_panel(right)

        status_bar = ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 4))
        status_bar.grid(row=3, column=0, sticky="ew")

    def _build_left_panel(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="文献库目录", font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.library_root_label = ttk.Label(frame, text=self.settings.library_root or "未设置", wraplength=240, justify="left")
        self.library_root_label.grid(row=1, column=0, sticky="ew", pady=(4, 12))

        subject_frame = ttk.LabelFrame(frame, text="主题筛选")
        subject_frame.grid(row=2, column=0, sticky="nsew")
        subject_frame.columnconfigure(0, weight=1)
        subject_frame.rowconfigure(0, weight=1)
        self.subject_list = tk.Listbox(subject_frame, exportselection=False, height=18)
        self.subject_list.grid(row=0, column=0, sticky="nsew")
        subject_scroll = ttk.Scrollbar(subject_frame, orient="vertical", command=self.subject_list.yview)
        subject_scroll.grid(row=0, column=1, sticky="ns")
        self.subject_list.config(yscrollcommand=subject_scroll.set)
        self.subject_list.bind("<<ListboxSelect>>", self.on_subject_selected)

        tips = (
            "实用功能：\n"
            "- 题名/作者/关键词全文搜索\n"
            "- 一条文献可关联多份原文/翻译/补充材料\n"
            "- 一个笔记可绑定多个附件\n"
            "- 批量导出 Bib 与 PDF 规范命名"
        )
        ttk.Label(frame, text=tips, justify="left").grid(row=3, column=0, sticky="ew", pady=(12, 0))
        frame.rowconfigure(2, weight=1)

    def _build_center_panel(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("title", "year", "type", "authors", "subject", "status", "attachments")
        self.literature_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "title": ("标题", 300),
            "year": ("年份", 70),
            "type": ("类型", 90),
            "authors": ("作者", 160),
            "subject": ("主题", 120),
            "status": ("阅读状态", 90),
            "attachments": ("附件数", 70),
        }
        for key, (text, width) in headings.items():
            self.literature_tree.heading(key, text=text)
            self.literature_tree.column(key, width=width, anchor="w")
        self.literature_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.literature_tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.literature_tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.literature_tree.config(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.literature_tree.bind("<<TreeviewSelect>>", lambda _event: self.on_literature_selected())
        self.literature_tree.bind("<Double-1>", lambda _event: self.edit_literature())

    def _build_right_panel(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        self.detail_title_var = tk.StringVar(value="请选择文献")
        self.detail_meta_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.detail_title_var, font=("Microsoft YaHei UI", 12, "bold"), wraplength=420).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.detail_meta_var, foreground="#666666", wraplength=420, justify="left").grid(row=1, column=0, sticky="w", pady=(4, 8))

        notebook = ttk.Notebook(frame)
        notebook.grid(row=2, column=0, sticky="nsew")

        meta_tab = ttk.Frame(notebook, padding=8)
        attachment_tab = ttk.Frame(notebook, padding=8)
        notes_tab = ttk.Frame(notebook, padding=8)
        notebook.add(meta_tab, text="详情")
        notebook.add(attachment_tab, text="附件")
        notebook.add(notes_tab, text="笔记")

        meta_tab.columnconfigure(0, weight=1)
        meta_tab.rowconfigure(0, weight=1)
        self.meta_text = scrolledtext.ScrolledText(meta_tab, wrap="word", state="disabled")
        self.meta_text.grid(row=0, column=0, sticky="nsew")

        attachment_tab.columnconfigure(0, weight=1)
        attachment_tab.rowconfigure(1, weight=1)
        attachment_buttons = ttk.Frame(attachment_tab)
        attachment_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(attachment_buttons, text="添加", command=self.add_attachments).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="打开", command=self.open_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="定位", command=self.reveal_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="删除", command=self.delete_attachment).pack(side="left")
        self.attachment_tree = ttk.Treeview(attachment_tab, columns=("label", "role", "language", "primary", "path"), show="headings", height=10)
        for key, text, width in (
            ("label", "文件名", 160),
            ("role", "角色", 70),
            ("language", "语言", 70),
            ("primary", "主文件", 60),
            ("path", "路径", 280),
        ):
            self.attachment_tree.heading(key, text=text)
            self.attachment_tree.column(key, width=width, anchor="w")
        self.attachment_tree.grid(row=1, column=0, sticky="nsew")
        attachment_scroll = ttk.Scrollbar(attachment_tab, orient="vertical", command=self.attachment_tree.yview)
        attachment_scroll.grid(row=1, column=1, sticky="ns")
        self.attachment_tree.config(yscrollcommand=attachment_scroll.set)

        notes_tab.columnconfigure(0, weight=1)
        notes_tab.rowconfigure(1, weight=1)
        notes_tab.rowconfigure(3, weight=1)
        note_buttons = ttk.Frame(notes_tab)
        note_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(note_buttons, text="新增文本", command=self.add_note).pack(side="left", padx=(0, 6))
        ttk.Button(note_buttons, text="关联文件", command=self.add_note_file).pack(side="left", padx=(0, 6))
        ttk.Button(note_buttons, text="编辑", command=self.edit_note).pack(side="left", padx=(0, 6))
        ttk.Button(note_buttons, text="打开文件", command=self.open_note_file).pack(side="left", padx=(0, 6))
        ttk.Button(note_buttons, text="删除", command=self.delete_note).pack(side="left")

        self.note_tree = ttk.Treeview(notes_tab, columns=("title", "type", "attachments", "updated"), show="headings", height=8)
        for key, text, width in (
            ("title", "标题", 180),
            ("type", "类型", 90),
            ("attachments", "关联附件", 80),
            ("updated", "更新时间", 140),
        ):
            self.note_tree.heading(key, text=text)
            self.note_tree.column(key, width=width, anchor="w")
        self.note_tree.grid(row=1, column=0, sticky="nsew")
        note_scroll = ttk.Scrollbar(notes_tab, orient="vertical", command=self.note_tree.yview)
        note_scroll.grid(row=1, column=1, sticky="ns")
        self.note_tree.config(yscrollcommand=note_scroll.set)
        self.note_tree.bind("<<TreeviewSelect>>", lambda _event: self.on_note_selected())
        self.note_tree.bind("<Double-1>", lambda _event: self.edit_note())

        ttk.Label(notes_tab, text="内容预览").grid(row=2, column=0, sticky="w", pady=(8, 4))
        self.note_preview = scrolledtext.ScrolledText(notes_tab, wrap="word", height=10, state="disabled")
        self.note_preview.grid(row=3, column=0, sticky="nsew")

    def refresh_filters(self) -> None:
        filters = self.db.list_filter_values()
        self.type_combo["values"] = [""] + [entry_type_label(code) for code in filters["entry_types"]]
        self.year_combo["values"] = [""] + filters["years"]
        previous_subject = self.subject_var.get()
        self.subject_list.delete(0, "end")
        self.subject_list.insert("end", "全部主题")
        selected_index = 0
        for index, subject in enumerate(filters["subjects"], start=1):
            self.subject_list.insert("end", subject)
            if subject == previous_subject:
                selected_index = index
        if selected_index == 0:
            self.subject_var.set("")
        self.subject_list.selection_clear(0, "end")
        self.subject_list.selection_set(selected_index)
        self.library_root_label.configure(text=self.settings.library_root or "未设置")

    def refresh_literatures(self) -> None:
        entry_code = ENTRY_TYPE_BY_LABEL.get(self.type_var.get(), "")
        rows = self.db.list_literatures(
            search=self.search_var.get().strip(),
            subject=self.subject_var.get().strip(),
            year=self.year_var.get().strip(),
            entry_type=entry_code,
        )
        existing_selection = set(self.get_selected_literature_ids())
        self.literature_tree.delete(*self.literature_tree.get_children())
        for row in rows:
            iid = str(row["id"])
            self.literature_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["title"],
                    row.get("year") or "",
                    entry_type_label(row.get("entry_type", "")),
                    row.get("authors_display", ""),
                    row.get("subject", ""),
                    row.get("reading_status", ""),
                    row.get("attachment_count", 0),
                ),
            )
            if row["id"] in existing_selection:
                self.literature_tree.selection_add(iid)
        self.status_var.set(f"当前显示 {len(rows)} 条文献")
        if self.literature_tree.selection():
            self.on_literature_selected()
        elif rows:
            first_id = str(rows[0]["id"])
            self.literature_tree.selection_set(first_id)
            self.on_literature_selected()
        else:
            self.current_literature_id = None
            self.current_detail = None
            self.clear_detail_panel()

    def clear_filters(self) -> None:
        self.search_var.set("")
        self.subject_var.set("")
        self.year_var.set("")
        self.type_var.set("")
        self.refresh_filters()
        self.refresh_literatures()

    def on_subject_selected(self, _event=None) -> None:
        selection = self.subject_list.curselection()
        if not selection:
            return
        value = self.subject_list.get(selection[0])
        self.subject_var.set("" if value == "全部主题" else value)
        self.refresh_literatures()

    def get_selected_literature_ids(self) -> list[int]:
        return [int(item) for item in self.literature_tree.selection()]

    def on_literature_selected(self) -> None:
        selected = self.get_selected_literature_ids()
        if not selected:
            return
        self.current_literature_id = selected[0]
        self.load_literature_detail(selected[0])

    def load_literature_detail(self, literature_id: int) -> None:
        detail = self.db.get_literature(literature_id)
        if not detail:
            self.clear_detail_panel()
            return
        self.current_detail = detail
        self.detail_title_var.set(detail.get("title", ""))
        meta = [entry_type_label(detail.get("entry_type", "")), str(detail.get("year") or ""), detail.get("subject", "")]
        tags = join_csv(detail.get("tags", []))
        if tags:
            meta.append(f"标签：{tags}")
        self.detail_meta_var.set(" | ".join(part for part in meta if part))

        lines = [
            f"标题：{detail.get('title', '')}",
            f"译名：{detail.get('translated_title', '')}",
            f"作者：{' / '.join(detail.get('authors', []))}",
            f"年份：{detail.get('year', '')}",
            f"主题：{detail.get('subject', '')}",
            f"关键词：{detail.get('keywords', '')}",
            f"来源：{detail.get('publication_title', '')}",
            f"出版社/机构：{detail.get('publisher', '') or detail.get('school', '')}",
            f"会议：{detail.get('conference_name', '')}",
            f"标准号：{detail.get('standard_number', '')}",
            f"专利号：{detail.get('patent_number', '')}",
            f"卷期页：{detail.get('volume', '')} / {detail.get('issue', '')} / {detail.get('pages', '')}",
            f"DOI：{detail.get('doi', '')}",
            f"ISBN：{detail.get('isbn', '')}",
            f"URL：{detail.get('url', '')}",
            f"语种：{detail.get('language', '')}",
            f"国家/地区：{detail.get('country', '')}",
            f"阅读状态：{detail.get('reading_status', '')}",
            f"评分：{detail.get('rating', '')}",
            f"引用键：{detail.get('cite_key', '')}",
            "",
            "一句话简介：",
            detail.get("summary", ""),
            "",
            "摘要：",
            detail.get("abstract", ""),
            "",
            "备注：",
            detail.get("remarks", ""),
        ]
        self._set_text(self.meta_text, "\n".join(lines).strip())

        self.attachment_tree.delete(*self.attachment_tree.get_children())
        for attachment in detail.get("attachments", []):
            self.attachment_tree.insert(
                "",
                "end",
                iid=str(attachment["id"]),
                values=(
                    attachment.get("label", ""),
                    role_label(attachment.get("role", "")),
                    attachment.get("language", ""),
                    "是" if attachment.get("is_primary") else "",
                    attachment.get("resolved_path", ""),
                ),
            )

        self.note_tree.delete(*self.note_tree.get_children())
        for note in detail.get("notes", []):
            self.note_tree.insert(
                "",
                "end",
                iid=str(note["id"]),
                values=(
                    note.get("title", ""),
                    f"{note_type_label(note.get('note_type', 'text'))}（{note_format_label(note.get('note_format', 'text'))}）"
                    if note.get("note_type") == "file"
                    else note_type_label(note.get("note_type", "text")),
                    note.get("attachment_count", 0),
                    note.get("updated_at", ""),
                ),
            )
        self._set_text(self.note_preview, "")

    def clear_detail_panel(self) -> None:
        self.detail_title_var.set("请选择文献")
        self.detail_meta_var.set("")
        self._set_text(self.meta_text, "")
        self.attachment_tree.delete(*self.attachment_tree.get_children())
        self.note_tree.delete(*self.note_tree.get_children())
        self._set_text(self.note_preview, "")

    def _set_text(self, widget: scrolledtext.ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _merge_metadata_payload(self, current: dict, incoming: dict) -> dict:
        payload = dict(current)
        for key, value in incoming.items():
            if key in {"id", "attachments", "notes"}:
                continue
            if key == "authors":
                if not payload.get("authors"):
                    payload["authors"] = value
            elif key == "tags":
                existing = list(payload.get("tags", []))
                for tag in value or []:
                    if tag not in existing:
                        existing.append(tag)
                payload["tags"] = existing
            elif value and not payload.get(key):
                payload[key] = value
        return payload

    def reload_database(self) -> None:
        self.db.close()
        self.db = LibraryDatabase(self.settings_store.database_path, lambda: self.settings.library_root)
        self.db.rebuild_search_index()
        self.refresh_filters()
        self.refresh_literatures()

    def open_import_center(self) -> None:
        dialog = ImportCenterDialog(self, self.settings)
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            result = import_scanned_items(
                self.db,
                dialog.result["items"],
                self.settings,
                import_mode=dialog.result["import_mode"],
            )
        except ValueError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        self.refresh_filters()
        self.refresh_literatures()
        self.status_var.set(f"已导入 {result['imported']} 条，跳过 {result['skipped']} 条")
        messagebox.showinfo("导入完成", f"已导入 {result['imported']} 条，跳过 {result['skipped']} 条。", parent=self)

    def fill_metadata(self) -> None:
        if not self.current_literature_id:
            messagebox.showinfo("提示", "请先选择一条文献。", parent=self)
            return
        detail = self.db.get_literature(self.current_literature_id)
        if not detail:
            return
        payload = None
        try:
            if detail.get("doi"):
                payload = lookup_doi(detail["doi"])
            elif detail.get("isbn"):
                payload = lookup_isbn(detail["isbn"])
            else:
                source_type = simpledialog.askstring("元数据补全", "请输入 DOI 或 ISBN：", parent=self)
                if not source_type:
                    return
                source_type = source_type.strip()
                if source_type.lower().startswith("10."):
                    payload = lookup_doi(source_type)
                else:
                    payload = lookup_isbn(source_type)
        except ValueError as exc:
            messagebox.showerror("补全失败", str(exc), parent=self)
            return
        if not payload:
            return
        preview = MetadataPreviewDialog(self, payload)
        self.wait_window(preview)
        if not preview.result:
            return
        merged = self._merge_metadata_payload(detail, payload)
        self.db.save_literature(merged)
        self.load_literature_detail(self.current_literature_id)
        self.refresh_literatures()
        self.status_var.set("元数据已补全")

    def open_dedupe_center(self) -> None:
        groups = find_duplicate_groups(self.db)
        if not groups:
            messagebox.showinfo("查重结果", "当前未检测到重复文献。", parent=self)
            return
        dialog = DuplicateDialog(self, groups)
        self.wait_window(dialog)
        if not dialog.result:
            return
        merge_literatures(self.db, dialog.result["primary_id"], dialog.result["merged_ids"], dialog.result["reason"])
        self.refresh_filters()
        self.refresh_literatures()
        self.literature_tree.selection_set(str(dialog.result["primary_id"]))
        self.on_literature_selected()
        self.status_var.set(f"已合并 {len(dialog.result['merged_ids'])} 条重复记录")

    def open_search_center(self) -> None:
        dialog = SearchDialog(self, self.db)
        self.wait_window(dialog)
        if not dialog.result:
            return
        if not self.literature_tree.exists(str(dialog.result)):
            self.clear_filters()
        self.literature_tree.selection_set(str(dialog.result))
        self.literature_tree.focus(str(dialog.result))
        self.on_literature_selected()

    def open_maintenance_tools(self) -> None:
        dialog = MaintenanceDialog(self, self.db, self.settings_store, self.settings)
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            self.settings = restore_backup(self.settings_store, dialog.result["restore_path"])
        except ValueError as exc:
            messagebox.showerror("恢复失败", str(exc), parent=self)
            return
        self.reload_database()
        messagebox.showinfo("恢复完成", "已恢复备份，当前界面已刷新。", parent=self)

    def open_statistics(self) -> None:
        dialog = StatisticsDialog(self, self.db)
        self.wait_window(dialog)

    def new_literature(self) -> None:
        dialog = LiteratureDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            literature_id = self.db.save_literature(dialog.result)
            self.refresh_filters()
            self.refresh_literatures()
            self.literature_tree.selection_set(str(literature_id))
            self.on_literature_selected()
            self.status_var.set("已新增文献")

    def edit_literature(self) -> None:
        if not self.current_literature_id:
            messagebox.showinfo("提示", "请先选择要编辑的文献。", parent=self)
            return
        detail = self.db.get_literature(self.current_literature_id)
        dialog = LiteratureDialog(self, detail)
        self.wait_window(dialog)
        if dialog.result:
            self.db.save_literature(dialog.result)
            self.refresh_filters()
            self.refresh_literatures()
            self.literature_tree.selection_set(str(self.current_literature_id))
            self.on_literature_selected()
            self.status_var.set("文献信息已更新")

    def delete_literature(self) -> None:
        selected = self.get_selected_literature_ids()
        if not selected:
            messagebox.showinfo("提示", "请先选择要删除的文献。", parent=self)
            return
        if not messagebox.askyesno("确认删除", f"确定删除选中的 {len(selected)} 条文献记录吗？\n此操作不会自动删除磁盘文件。", parent=self):
            return
        for literature_id in selected:
            self.db.delete_literature(literature_id)
        self.refresh_filters()
        self.refresh_literatures()
        self.status_var.set(f"已删除 {len(selected)} 条文献")

    def add_attachments(self) -> None:
        if not self.current_literature_id:
            messagebox.showinfo("提示", "请先选择一条文献。", parent=self)
            return
        files = filedialog.askopenfilenames(parent=self, title="选择要关联的文件")
        if not files:
            return
        dialog = AttachmentImportDialog(self, self.settings, len(files))
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            self.db.add_attachments(self.current_literature_id, list(files), **dialog.result)
        except ValueError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        self.load_literature_detail(self.current_literature_id)
        self.refresh_literatures()
        self.status_var.set(f"已导入 {len(files)} 个附件")

    def _selected_attachment_id(self) -> int | None:
        selection = self.attachment_tree.selection()
        return int(selection[0]) if selection else None

    def open_attachment(self) -> None:
        attachment_id = self._selected_attachment_id()
        if not attachment_id:
            messagebox.showinfo("提示", "请先选择一个附件。", parent=self)
            return
        attachment = self.db.get_attachment(attachment_id)
        if not attachment:
            return
        try:
            preferred_app = self.settings.pdf_reader_path if Path(attachment["resolved_path"]).suffix.lower() == ".pdf" else ""
            open_path(attachment["resolved_path"], preferred_app=preferred_app)
        except FileNotFoundError:
            if self.settings.pdf_reader_path and Path(attachment["resolved_path"]).suffix.lower() == ".pdf":
                message = f"文件或阅读器不存在：\n{attachment['resolved_path']}\n{self.settings.pdf_reader_path}"
            else:
                message = attachment["resolved_path"]
            messagebox.showerror("文件不存在", message, parent=self)

    def reveal_attachment(self) -> None:
        attachment_id = self._selected_attachment_id()
        if not attachment_id:
            messagebox.showinfo("提示", "请先选择一个附件。", parent=self)
            return
        attachment = self.db.get_attachment(attachment_id)
        if not attachment:
            return
        try:
            reveal_path(attachment["resolved_path"])
        except FileNotFoundError:
            messagebox.showerror("路径不存在", attachment["resolved_path"], parent=self)

    def delete_attachment(self) -> None:
        attachment_id = self._selected_attachment_id()
        if not attachment_id:
            messagebox.showinfo("提示", "请先选择一个附件。", parent=self)
            return
        attachment = self.db.get_attachment(attachment_id)
        if not attachment:
            return
        choice = messagebox.askyesnocancel(
            "删除附件",
            "是否同时删除磁盘文件？\n选择“是”会删除文件并取消关联；选择“否”只删除关联记录。",
            parent=self,
        )
        if choice is None:
            return
        self.db.delete_attachment(attachment_id, delete_file=choice)
        if self.current_literature_id:
            self.load_literature_detail(self.current_literature_id)
            self.refresh_literatures()
        self.status_var.set("附件已删除")

    def add_note(self) -> None:
        if not self.current_literature_id or not self.current_detail:
            messagebox.showinfo("提示", "请先选择一条文献。", parent=self)
            return
        dialog = NoteDialog(self, self.current_detail.get("attachments", []))
        self.wait_window(dialog)
        if not dialog.result:
            return
        self.db.save_note(
            literature_id=self.current_literature_id,
            title=dialog.result["title"],
            content=dialog.result["content"],
            attachment_ids=dialog.result["attachment_ids"],
            note_type=dialog.result.get("note_type", "text"),
        )
        self.load_literature_detail(self.current_literature_id)
        self.status_var.set("笔记已保存")

    def add_note_file(self) -> None:
        if not self.current_literature_id or not self.current_detail:
            messagebox.showinfo("提示", "请先选择一条文献。", parent=self)
            return
        dialog = NoteFileDialog(self, self.settings, self.current_detail.get("attachments", []))
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            self.db.save_note(
                literature_id=self.current_literature_id,
                title=dialog.result["title"],
                content=dialog.result.get("content", ""),
                attachment_ids=dialog.result["attachment_ids"],
                note_type=dialog.result["note_type"],
                note_format=dialog.result["note_format"],
                external_file_path=dialog.result["external_file_path"],
                import_mode=dialog.result["import_mode"],
            )
        except ValueError as exc:
            messagebox.showerror("关联失败", str(exc), parent=self)
            return
        self.load_literature_detail(self.current_literature_id)
        self.status_var.set("已关联笔记文件")

    def _selected_note_id(self) -> int | None:
        selection = self.note_tree.selection()
        return int(selection[0]) if selection else None

    def on_note_selected(self) -> None:
        note_id = self._selected_note_id()
        if not note_id:
            self._set_text(self.note_preview, "")
            return
        note = self.db.get_note(note_id)
        if not note:
            return
        if note.get("note_type") == "file":
            preview = load_note_preview(note.get("resolved_path", ""))
        else:
            preview = note.get("content", "")
        self._set_text(self.note_preview, preview)

    def open_note_file(self) -> None:
        note_id = self._selected_note_id()
        if not note_id:
            messagebox.showinfo("提示", "请先选择一条笔记。", parent=self)
            return
        note = self.db.get_note(note_id)
        if not note or note.get("note_type") != "file":
            messagebox.showinfo("提示", "当前选中的不是文件笔记。", parent=self)
            return
        try:
            open_path(note.get("resolved_path", ""))
        except FileNotFoundError:
            messagebox.showerror("文件不存在", note.get("resolved_path", ""), parent=self)

    def edit_note(self) -> None:
        note_id = self._selected_note_id()
        if not note_id or not self.current_literature_id or not self.current_detail:
            messagebox.showinfo("提示", "请先选择一条笔记。", parent=self)
            return
        note = self.db.get_note(note_id)
        if not note:
            return
        if note.get("note_type") == "file":
            dialog = NoteFileDialog(self, self.settings, self.current_detail.get("attachments", []), note=note)
        else:
            dialog = NoteDialog(self, self.current_detail.get("attachments", []), note=note)
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            self.db.save_note(
                literature_id=self.current_literature_id,
                title=dialog.result["title"],
                content=dialog.result.get("content", ""),
                attachment_ids=dialog.result["attachment_ids"],
                note_id=note_id,
                note_type=dialog.result.get("note_type", "text"),
                note_format=dialog.result.get("note_format", "text"),
                external_file_path=dialog.result.get("external_file_path", ""),
                import_mode=dialog.result.get("import_mode", "link"),
            )
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.load_literature_detail(self.current_literature_id)
        self.status_var.set("笔记已更新")

    def delete_note(self) -> None:
        note_id = self._selected_note_id()
        if not note_id:
            messagebox.showinfo("提示", "请先选择一条笔记。", parent=self)
            return
        note = self.db.get_note(note_id)
        if not note:
            return
        delete_file = False
        if note.get("note_type") == "file":
            choice = messagebox.askyesnocancel(
                "删除笔记文件",
                "是否同时删除磁盘上的笔记文件？\n选择“是”删除文件，选择“否”只删除关联记录。",
                parent=self,
            )
            if choice is None:
                return
            delete_file = choice
        elif not messagebox.askyesno("确认删除", "确定删除这条笔记吗？", parent=self):
            return
        self.db.delete_note(note_id, delete_file=delete_file)
        if self.current_literature_id:
            self.load_literature_detail(self.current_literature_id)
        self.status_var.set("笔记已删除")

    def export_bib(self) -> None:
        selected = self.get_selected_literature_ids() or ([self.current_literature_id] if self.current_literature_id else [])
        selected = [item for item in selected if item]
        if not selected:
            messagebox.showinfo("提示", "请先选择至少一条文献。", parent=self)
            return
        initial_dir = self.settings.recent_export_dir or str(Path.cwd())
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 Bib 文件",
            defaultextension=".bib",
            filetypes=[("BibTeX 文件", "*.bib")],
            initialdir=initial_dir,
            initialfile="literature_export.bib",
        )
        if not path:
            return
        count = self.db.export_bib(selected, path)
        self.settings.recent_export_dir = str(Path(path).parent)
        self.settings_store.save(self.settings)
        self.status_var.set(f"已导出 {count} 条文献到 {path}")
        messagebox.showinfo("导出完成", f"已导出 {count} 条文献。", parent=self)

    def copy_gbt_reference(self) -> None:
        selected = self.get_selected_literature_ids() or ([self.current_literature_id] if self.current_literature_id else [])
        selected = [item for item in selected if item]
        if not selected:
            messagebox.showinfo("提示", "请先选择至少一条文献。", parent=self)
            return
        references = []
        for literature_id in selected:
            detail = self.db.get_literature(literature_id)
            if detail:
                references.append(build_gbt_reference(detail))
        text = "\n".join(reference for reference in references if reference)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set(f"已复制 {len(references)} 条国标参考文献")
        messagebox.showinfo("复制完成", "已复制到剪贴板。", parent=self)

    def export_csl_json(self) -> None:
        selected = self.get_selected_literature_ids() or ([self.current_literature_id] if self.current_literature_id else [])
        selected = [item for item in selected if item]
        if not selected:
            messagebox.showinfo("提示", "请先选择至少一条文献。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 CSL JSON",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
            initialfile="literature_export_csl.json",
        )
        if not path:
            return
        entries = []
        for literature_id in selected:
            detail = self.db.get_literature(literature_id)
            if detail:
                entries.append(build_csl_entry(detail))
        Path(path).write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_var.set(f"已导出 {len(entries)} 条 CSL JSON")
        messagebox.showinfo("导出完成", f"已导出 {len(entries)} 条记录。", parent=self)

    def rename_pdfs(self) -> None:
        selected = self.get_selected_literature_ids() or ([self.current_literature_id] if self.current_literature_id else [])
        selected = [item for item in selected if item]
        if not selected:
            messagebox.showinfo("提示", "请先选择至少一条文献。", parent=self)
            return
        previews = self.db.preview_pdf_renames(selected)
        if not previews:
            messagebox.showinfo("无可重命名文件", "所选文献下没有可处理的 PDF 附件。", parent=self)
            return
        dialog = RenamePreviewDialog(self, previews)
        self.wait_window(dialog)
        if not dialog.result:
            return
        renamed = self.db.apply_pdf_renames(previews)
        if self.current_literature_id:
            self.load_literature_detail(self.current_literature_id)
        self.refresh_literatures()
        self.status_var.set(f"已重命名 {renamed} 个 PDF 文件")
        messagebox.showinfo("执行完成", f"已重命名 {renamed} 个 PDF 文件。", parent=self)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.settings)
        self.wait_window(dialog)
        if not dialog.result:
            return
        self.settings.library_root = dialog.result.library_root
        self.settings.default_import_mode = dialog.result.default_import_mode
        self.settings.pdf_reader_path = dialog.result.pdf_reader_path
        if dialog.result.recent_export_dir:
            self.settings.recent_export_dir = dialog.result.recent_export_dir
        self.settings_store.save(self.settings)
        self.refresh_filters()
        self.refresh_literatures()
        if self.current_literature_id:
            self.load_literature_detail(self.current_literature_id)
        self.status_var.set("设置已保存")
