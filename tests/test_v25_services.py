import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController
from literature_manager.ocr_service import (
    extract_pdf_text_with_ocr,
    read_umi_ocr_server_port,
    select_umi_ocr_asset,
)
from literature_manager.update_service import check_latest_release


@contextmanager
def build_controller(root: Path):
    patcher = mock.patch.dict("os.environ", {"LITERATURE_MANAGER_HOME": str(root / "app_home")})
    patcher.start()
    controller = None
    try:
        store = SettingsStore()
        settings = AppSettings(library_root=str(root / "library"))
        store.save(settings)
        controller = LibraryController(store, settings, auto_rebuild_index=False)
        yield controller
    finally:
        if controller is not None:
            controller.close()
        patcher.stop()


class MultiLibraryTests(unittest.TestCase):
    def test_settings_store_supports_archive_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            patcher = mock.patch.dict("os.environ", {"LITERATURE_MANAGER_HOME": tmp})
            patcher.start()
            self.addCleanup(patcher.stop)
            store = SettingsStore()
            store.create_profile("第二文库")
            store.switch_profile("第二文库")
            profile = store.current_profile()
            self.assertEqual(profile.name, "第二文库")
            archived = store.set_profile_archived("默认文献库", True)
            self.assertTrue(archived.archived)


class MetadataFallbackTests(unittest.TestCase):
    def test_controller_falls_back_to_title_lookup_when_doi_lookup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                literature_id = controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Fallback Title",
                        "year": 2024,
                        "doi": "10.1000/fail",
                        "authors": ["Alice Example"],
                        "tags": [],
                    }
                )
                with mock.patch(
                    "literature_manager.controllers.library_controller.lookup_doi",
                    side_effect=ValueError("DOI 查询失败"),
                ), mock.patch(
                    "literature_manager.controllers.library_controller.lookup_title_metadata",
                    return_value={"title": "Fallback Title", "source_provider": "OpenAlex"},
                ):
                    _detail, payload = controller.lookup_metadata_for_literature(literature_id)
                self.assertEqual(payload["source_provider"], "OpenAlex")
                self.assertIn("metadata_lookup_notice", payload)


class OcrAndUpdateTests(unittest.TestCase):
    def test_extract_pdf_text_with_ocr_returns_original_when_no_command(self):
        settings = AppSettings()
        self.assertEqual(extract_pdf_text_with_ocr("demo.pdf", "short text", settings), "short text")

    def test_select_umi_ocr_asset_prefers_requested_variant(self):
        assets = [
            {"name": "Umi-OCR_Paddle_v2.1.5.7z.exe", "browser_download_url": "https://example.com/paddle.exe"},
            {"name": "Umi-OCR_Rapid_v2.1.5.7z.exe", "browser_download_url": "https://example.com/rapid.exe"},
        ]
        selected = select_umi_ocr_asset(assets, "rapid")
        self.assertIn("Rapid", selected["name"])

    def test_read_umi_ocr_server_port_uses_pre_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Umi-OCR"
            data_dir = root / "UmiOCR-data"
            data_dir.mkdir(parents=True)
            exe_path = root / "Umi-OCR.exe"
            exe_path.write_text("", encoding="utf-8")
            (data_dir / ".pre_settings").write_text(
                '{"server_port": 1326}',
                encoding="utf-8",
            )
            self.assertEqual(read_umi_ocr_server_port(exe_path), 1326)

    def test_check_latest_release_prefers_setup_asset(self):
        payload = {
            "tag_name": "v0.3.1",
            "name": "v0.3.1",
            "published_at": "2026-03-15T00:00:00Z",
            "html_url": "https://example.com/release",
            "body": "release notes",
            "assets": [
                {"name": "portable.zip", "browser_download_url": "https://example.com/portable.zip"},
                {"name": "LiteratureManagementTool-Setup.exe", "browser_download_url": "https://example.com/setup.exe"},
            ],
        }
        with mock.patch("literature_manager.update_service._get_json", return_value=payload):
            result = check_latest_release("owner/repo", "0.3.0")
        self.assertTrue(result["is_update_available"])
        self.assertTrue(result["asset_name"].endswith("Setup.exe"))


if __name__ == "__main__":
    unittest.main()
