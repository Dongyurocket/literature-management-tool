from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .config import SettingsStore
from .db import LibraryDatabase
from .ui import MainWindow


def main() -> None:
    settings_store = SettingsStore()
    settings = settings_store.load()
    database = LibraryDatabase(settings_store.database_path, lambda: settings.library_root)

    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    root.title("文献管理软件")
    root.geometry("1500x900")

    window = MainWindow(root, database, settings_store, settings)
    window.pack(fill="both", expand=True)

    def on_close() -> None:
        settings_store.save(window.settings)
        database.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
