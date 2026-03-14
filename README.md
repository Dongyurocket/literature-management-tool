# Literature management tool

`Literature management tool` is a local-first desktop application for managing academic literature, PDFs, notes, BibTeX data, and full-text search. It is built with `tkinter + sqlite3 + pypdf`, works well on Windows, and keeps your data on your own machine by default.

## Overview

This project is designed for researchers, graduate students, engineers, and anyone who needs a practical reference manager without setting up a server.

The application focuses on five things:

1. structured literature metadata
2. reliable attachment management
3. practical note-taking workflows
4. export and citation support
5. long-term maintainability of local data

## Core Capabilities

### 1. Literature Metadata Management

Each literature record supports common fields needed for GB/T 7714-2015 style reference management, including:

- entry type
- title
- translated title / subtitle
- authors in order
- journal / book / conference information
- publisher / school / institution
- year, month, volume, issue, pages
- DOI / ISBN / URL / language / country
- subject, keywords, summary, abstract, remarks
- reading status, rating, tags, cite key

Supported entry types include:

- journal article
- book
- thesis
- conference paper
- standard
- patent
- report
- webpage
- misc

### 2. Attachment Management

A single literature record can contain multiple attachments, such as:

- original PDF
- translated PDF or manuscript
- supplementary material
- note files

Import modes:

- `copy`: copy selected files into the configured library root
- `move`: move selected files into the configured library root
- `link`: keep files in place and only store references

The application also supports standardized PDF renaming using a rule like:

- `Author_Year_Title_Original.pdf`
- `Author_Year_Title_Translation.pdf`

### 3. Notes

Two note models are supported:

- built-in text notes stored in the database
- external note files linked to the literature record

Supported external note file formats:

- `docx`
- `md`
- `txt`

A note can be linked to multiple attachments, which is useful if one reading note belongs to several related files.

### 4. Metadata Import and Enrichment

The app can enrich metadata using:

- DOI lookup
- ISBN lookup

It can also import from:

- `bib`
- `ris`
- `pdf`
- `docx`
- `md`
- `txt`

Batch import is supported through the Import Center.

### 5. Search, Deduplication, and Maintenance

V2 includes:

- duplicate detection and merge
- full-text search across metadata, notes, docx files, and extracted PDF text
- missing path detection
- repair by scanning a replacement folder
- backup and restore
- search index rebuild
- statistics dashboard

### 6. Citation Export

The application supports:

- BibTeX export
- CSL JSON export
- copying GB/T reference text directly to clipboard

## Technology Stack

- UI: `tkinter`
- storage: `sqlite3`
- PDF parsing: `pypdf`
- packaging: `PyInstaller`
- release automation: GitHub Actions

## Repository Structure

```text
literature-management-tool/
|- literature_manager/
|  |- app.py
|  |- config.py
|  |- db.py
|  |- dedupe_service.py
|  |- import_service.py
|  |- maintenance_service.py
|  |- metadata_service.py
|  |- ui.py
|  |- utils.py
|- tests/
|- scripts/
|  |- build_windows.ps1
|- .github/workflows/
|  |- build-windows-release.yml
|- LiteratureManagementTool.spec
|- main.py
|- pyproject.toml
|- README.md
```

## Running From Source

### Requirements

- Windows 10 or Windows 11 recommended
- Python `3.11+`
- network access only needed when using DOI / ISBN lookup

### Install dependencies

```bash
python -m pip install -U pip
python -m pip install pypdf
```

If you want to run packaging locally:

```bash
python -m pip install pyinstaller
```

### Start the app

```bash
python main.py
```

## Data Storage

Application data is stored outside the repository by default.

Default path:

- `%APPDATA%\Literature management tool`

Contents typically include:

- `library.sqlite3`
- `settings.json`
- restored files after backup restore

You can override the application data directory by setting:

- `LITERATURE_MANAGER_HOME`

## First-Run Setup

Recommended first steps:

1. open `Settings`
2. choose a `library root`
3. choose the default import mode
4. optionally configure a custom PDF reader executable
5. start importing literature

## Main Workflows

### Create a literature record manually

1. click `新建文献`
2. fill in metadata fields
3. add authors in order
4. save the record
5. attach PDFs or supplementary files

### Batch import existing files

1. click `导入中心`
2. select files or a folder
3. review scanned items
4. choose import mode
5. import selected items

### Enrich metadata with DOI / ISBN

1. select a literature record
2. click `元数据补全`
3. if DOI or ISBN already exists, the app uses it directly
4. otherwise input DOI or ISBN manually
5. review the preview dialog
6. apply missing-field updates

### Attach files

1. select a literature record
2. click `添加附件`
3. select one or more files
4. choose role and import mode
5. save

### Add notes

For text notes:

1. go to the `笔记` tab
2. click `新增文本`
3. write the note
4. optionally link attachments

For external note files:

1. go to the `笔记` tab
2. click `关联文件`
3. choose `docx`, `md`, or `txt`
4. optionally link attachments

### Search across the library

1. click `全文搜索`
2. input keywords
3. double-click a result to jump to the record

### Merge duplicates

1. click `查重`
2. review duplicate groups
3. select the primary record to keep
4. merge the remaining records into it

### Repair missing file paths

1. click `维护`
2. refresh missing files
3. click repair
4. select the folder that may contain the moved files

### Backup and restore

In `维护`:

- `创建备份` creates a zip containing database, settings, and library files if configured
- `恢复备份` restores those items and refreshes the app state

## Full-Text Search Scope

The search index includes:

- title
- translated title
- authors
- subject
- keywords
- summary
- abstract
- built-in note text
- external note file text
- extracted PDF text when available

## PDF Reader Integration

If a custom PDF reader executable is configured in `Settings`, opening a PDF attachment uses that program first. If not configured, the system default handler is used.

## Windows Packaging

### Build locally

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -Version 0.2.1
```

Generated outputs:

- `dist\Literature management tool\` - unpacked runnable folder
- `dist\Literature-management-tool-v0.2.1-windows-x64.zip` - release archive

### Packaging files

- `LiteratureManagementTool.spec`
- `scripts/build_windows.ps1`
- `.github/workflows/build-windows-release.yml`

### Notes

- the current release format is a zipped portable build, not an installer
- the executable is built with PyInstaller in windowed mode
- README is copied into the packaged output folder

## GitHub Release Workflow

A GitHub Actions workflow is included.

When a tag like `v0.2.1` is pushed:

1. GitHub Actions checks out the repo on Windows
2. installs Python and dependencies
3. runs the PowerShell build script
4. uploads the generated zip to the matching GitHub Release

Workflow file:

- `.github/workflows/build-windows-release.yml`

## Testing

Run the full test suite with:

```bash
python -m unittest discover -s tests -v
```

Optional syntax check:

```bash
python -m compileall main.py literature_manager
```

## Current Limitations

- no installer wizard yet; distribution is a portable zip
- OCR is not implemented
- PDF metadata extraction is best-effort only
- duplicate merge is intentionally conservative and UI-assisted
- there is no cloud sync in the current version

## Security and Privacy

- literature data is stored locally by default
- DOI / ISBN enrichment sends only lookup identifiers to external services
- backup archives may contain your documents; store them securely
- if you make the repository public, review the repo contents carefully before publishing future changes

## Suggested Next Improvements

- add an installer-based Windows setup package
- add OCR for scanned PDFs
- improve duplicate conflict resolution UI
- add richer metadata provider fallback logic
- add export templates and report generation

## Version

Current packaged release target in this repository:

- `v0.2.1`
