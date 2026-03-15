import tempfile
import unittest
from pathlib import Path
from unittest import mock

from literature_manager import desktop


class DesktopTests(unittest.TestCase):
    def test_open_parent_folder_opens_parent_for_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.pdf"
            target.write_text("test", encoding="utf-8")

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.os, "startfile", create=True) as startfile,
            ):
                desktop.open_parent_folder(str(target))

            parent_dir = str(target.resolve().parent)
            startfile.assert_called_once_with(parent_dir)

    def test_open_parent_folder_opens_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.os, "startfile", create=True) as startfile,
            ):
                desktop.open_parent_folder(str(target))

            startfile.assert_called_once_with(str(target.resolve()))

    def test_open_parent_folder_raises_for_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "missing.pdf"

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.os, "startfile", create=True) as startfile,
            ):
                with self.assertRaises(FileNotFoundError):
                    desktop.open_parent_folder(str(target))

            startfile.assert_not_called()

    def test_open_parent_folder_handles_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            spaced_dir = Path(tmp) / "my library"
            spaced_dir.mkdir()
            target = spaced_dir / "sample paper.pdf"
            target.write_text("test", encoding="utf-8")

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.os, "startfile", create=True) as startfile,
            ):
                desktop.open_parent_folder(str(target))

            parent_dir = str(target.resolve().parent)
            startfile.assert_called_once_with(parent_dir)

    def test_open_parent_folder_handles_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            cn_dir = Path(tmp) / "文献库"
            cn_dir.mkdir()
            target = cn_dir / "论文.pdf"
            target.write_text("test", encoding="utf-8")

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.os, "startfile", create=True) as startfile,
            ):
                desktop.open_parent_folder(str(target))

            parent_dir = str(target.resolve().parent)
            startfile.assert_called_once_with(parent_dir)


if __name__ == "__main__":
    unittest.main()
