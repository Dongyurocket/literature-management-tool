import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController
from literature_manager.metadata_service import extract_partial_metadata_from_html, lookup_title_metadata
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

    def test_manual_identifier_overrides_existing_doi(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                literature_id = controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Override Identifier",
                        "year": 2024,
                        "doi": "10.1000/original",
                        "authors": ["Alice Example"],
                        "tags": [],
                    }
                )
                with mock.patch(
                    "literature_manager.controllers.library_controller.lookup_doi"
                ) as lookup_doi_mock, mock.patch(
                    "literature_manager.controllers.library_controller.lookup_isbn",
                    return_value={"title": "ISBN Result", "source_provider": "OpenLibrary"},
                ) as lookup_isbn_mock:
                    _detail, payload = controller.lookup_metadata_for_literature(
                        literature_id,
                        manual_identifier="9787300000000",
                    )
                lookup_doi_mock.assert_not_called()
                lookup_isbn_mock.assert_called_once()
                self.assertEqual(payload["source_provider"], "OpenLibrary")

    def test_controller_uses_only_one_selected_metadata_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            with build_controller(Path(tmp)) as controller:
                controller.settings.metadata_sources = ["crossref", "openalex", "cnki"]
                literature_id = controller.save_literature(
                    {
                        "entry_type": "journal_article",
                        "title": "Single Provider",
                        "year": 2025,
                        "doi": "10.1000/demo",
                        "authors": ["Alice Example"],
                        "tags": [],
                    }
                )
                with mock.patch(
                    "literature_manager.controllers.library_controller.lookup_doi",
                    return_value={"title": "Single Provider", "source_provider": "Crossref"},
                ) as lookup_doi_mock:
                    _detail, payload = controller.lookup_metadata_for_literature(literature_id)
                lookup_doi_mock.assert_called_once_with(
                    "10.1000/demo",
                    preferred_sources=["crossref"],
                )
                self.assertEqual(payload["source_provider"], "Crossref")


class MetadataProviderParsingTests(unittest.TestCase):
    def test_extract_partial_metadata_from_html_parses_sfx_context(self):
        html_text = """
        <html>
          <body>
            <!-- <ctx_object_1>$VAR1 = bless( {
                 |rft.genre| => |article|,
                 |rft.atitle| => |Attention Is All You Need|,
                 |rft.jtitle| => |arXiv|,
                 |rft.date| => |2017|,
                 |@rft.au| => [
                                |Vaswani, Ashish|
                              ]
               }, |ContextObject::Generic| );
            </ctx_object_1> //-->
          </body>
        </html>
        """
        payload = extract_partial_metadata_from_html(
            html_text,
            "http://resolver.example/openurl",
            "USTC OpenURL",
        )
        self.assertEqual(payload["title"], "Attention Is All You Need")
        self.assertEqual(payload["publication_title"], "arXiv")
        self.assertEqual(payload["year"], 2017)
        self.assertIn("Vaswani, Ashish", payload["authors"])

    def test_lookup_title_metadata_can_parse_cnki_html(self):
        search_html = """
        <html>
          <body>
            <a class="fz14" href="/detail/test">中文文献标题</a>
            <span class="author">张三;李四</span>
            <span class="date">2024</span>
          </body>
        </html>
        """
        detail_html = """
        <html>
          <head>
            <meta name="citation_title" content="中文文献标题" />
            <meta name="citation_author" content="张三" />
            <meta name="citation_author" content="李四" />
            <meta name="citation_journal_title" content="测试期刊" />
            <meta name="citation_publication_date" content="2024-03-01" />
            <meta name="citation_doi" content="10.1000/cnki-demo" />
            <meta name="citation_abstract_html_url" content="https://kns.cnki.net/detail/test" />
            <meta name="citation_keywords" content="人工智能;知识图谱" />
            <meta name="description" content="这是一段中文摘要。" />
          </head>
        </html>
        """
        with mock.patch(
            "literature_manager.metadata_service._safe_get_text",
            side_effect=[search_html, detail_html],
        ):
            payload = lookup_title_metadata(
                "中文文献标题",
                authors=["张三"],
                year=2024,
                preferred_sources=["cnki"],
            )
        self.assertEqual(payload["source_provider"], "CNKI")
        self.assertEqual(payload["publication_title"], "测试期刊")
        self.assertEqual(payload["doi"], "10.1000/cnki-demo")
        self.assertIn("张三", payload["authors"])
        self.assertIn("人工智能", payload["keywords"])
        self.assertIn("kw=", payload["metadata_search_url"])


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
