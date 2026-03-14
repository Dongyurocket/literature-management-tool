# Literature management tool

A local-first desktop application for managing literature, PDFs, notes, BibTeX, and full-text search, built with `tkinter + sqlite3 + pypdf`.

## Features

- Local-first library management with a custom storage folder
- GB/T 7714-2015 metadata fields plus subject, keywords, summary, abstract, rating, tags, and reading status
- Multiple attachments per literature record, including source PDFs, translations, note files, and supplements
- Text notes and external note files (`docx`, `md`, `txt`) with multi-attachment linking
- Custom PDF reader integration
- BibTeX export, CSL JSON export, and GB/T reference text copy
- DOI / ISBN metadata lookup
- Batch import for `pdf`, `bib`, `ris`, `docx`, `md`, and `txt`
- Duplicate detection and merge
- Full-text search across metadata, notes, docx files, and extracted PDF text
- Maintenance tools for missing path checks, repair, backup, restore, and index rebuild
- Statistics dashboard by year, subject, and reading status

## Run From Source

```bash
python main.py
```

Application data is stored in:

- Default: `%APPDATA%\\Literature management tool`
- Optional override: set `LITERATURE_MANAGER_HOME`

## Build Windows Executable

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

The build script creates:

- `dist\Literature management tool\` - unpacked Windows app folder
- `dist\Literature-management-tool-vX.Y.Z-windows-x64.zip` - release-ready archive

## Release Workflow

1. Build with `scripts/build_windows.ps1`
2. Create a git tag such as `v0.2.1`
3. Upload the generated zip to a GitHub Release

## Backup Notes

- Backup archives include the database, settings, and library files when a library folder is configured
- Restore extracts files to the application data directory under `restored_library`
