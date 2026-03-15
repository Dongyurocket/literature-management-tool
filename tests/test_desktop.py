import tempfile
import unittest
from pathlib import Path
from unittest import mock

from literature_manager import desktop


class DesktopTests(unittest.TestCase):
    def test_reveal_path_uses_windows_select_argument_for_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.pdf"
            target.write_text("test", encoding="utf-8")

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.subprocess, "Popen") as popen,
            ):
                desktop.reveal_path(str(target))

            normalized = desktop.os.path.normpath(str(target.resolve()))
            popen.assert_called_once_with(f'explorer /select,"{normalized}"')

    def test_reveal_path_opens_directory_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.subprocess, "Popen") as popen,
            ):
                desktop.reveal_path(str(target))

            popen.assert_called_once_with(["explorer", desktop.os.path.normpath(str(target.resolve()))])

    def test_reveal_path_raises_for_missing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "missing.pdf"

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.subprocess, "Popen") as popen,
            ):
                with self.assertRaises(FileNotFoundError):
                    desktop.reveal_path(str(target))

            popen.assert_not_called()

    def test_reveal_path_handles_spaces_in_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            spaced_dir = Path(tmp) / "my library"
            spaced_dir.mkdir()
            target = spaced_dir / "sample paper.pdf"
            target.write_text("test", encoding="utf-8")

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.subprocess, "Popen") as popen,
            ):
                desktop.reveal_path(str(target))

            normalized = desktop.os.path.normpath(str(target.resolve()))
            popen.assert_called_once_with(f'explorer /select,"{normalized}"')

    def test_reveal_path_handles_chinese_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            cn_dir = Path(tmp) / "文献库"
            cn_dir.mkdir()
            target = cn_dir / "论文.pdf"
            target.write_text("test", encoding="utf-8")

            with (
                mock.patch.object(desktop.os, "name", "nt"),
                mock.patch.object(desktop.subprocess, "Popen") as popen,
            ):
                desktop.reveal_path(str(target))

            normalized = desktop.os.path.normpath(str(target.resolve()))
            popen.assert_called_once_with(f'explorer /select,"{normalized}"')


if __name__ == "__main__":
    unittest.main()
