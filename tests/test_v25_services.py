import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import timedelta, timezone
from pathlib import Path
from urllib import error
from unittest import mock

from literature_manager.config import AppSettings, SettingsStore
from literature_manager.controllers import LibraryController
from literature_manager.metadata_service import (
    extract_partial_metadata_from_html,
    lookup_doi,
    lookup_title_metadata,
)
from literature_manager.ocr_service import (
    extract_pdf_text_with_ocr,
    read_umi_ocr_server_port,
    select_umi_ocr_asset,
)
from literature_manager.update_service import _build_fallback_notice, _format_published_at, check_latest_release


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

    def test_controller_passes_selected_metadata_sources_in_order(self):
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
                    preferred_sources=["crossref", "openalex", "cnki"],
                )
                self.assertEqual(payload["source_provider"], "Crossref")

    def test_lookup_doi_tries_selected_sources_in_order_until_success(self):
        call_order: list[str] = []

        def crossref_lookup(_doi: str) -> dict:
            call_order.append("crossref")
            raise ValueError("Crossref unavailable")

        def openalex_lookup(_doi: str) -> dict:
            call_order.append("openalex")
            return {"title": "Ordered Result", "source_provider": "OpenAlex"}

        def cnki_lookup(_doi: str) -> dict:
            call_order.append("cnki")
            return {"title": "Should Not Run", "source_provider": "CNKI"}

        with mock.patch(
            "literature_manager.metadata_service._lookup_doi_crossref",
            side_effect=crossref_lookup,
        ), mock.patch(
            "literature_manager.metadata_service._lookup_doi_openalex",
            side_effect=openalex_lookup,
        ), mock.patch(
            "literature_manager.metadata_service._lookup_doi_cnki",
            side_effect=cnki_lookup,
        ):
            payload = lookup_doi(
                "10.1000/demo",
                preferred_sources=["crossref", "openalex", "cnki"],
            )

        self.assertEqual(call_order, ["crossref", "openalex"])
        self.assertEqual(payload["source_provider"], "OpenAlex")
        self.assertEqual(
            payload["metadata_fallback_chain"],
            ["crossref", "openalex", "cnki"],
        )


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
    def test_metadata_service_import_tolerates_pil_without_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            pil_dir = Path(tmp) / "PIL"
            pil_dir.mkdir()
            (pil_dir / "__init__.py").write_text("", encoding="utf-8")

            env = os.environ.copy()
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                tmp if not existing_pythonpath else os.pathsep.join([tmp, existing_pythonpath])
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import literature_manager.metadata_service; import PIL; print(PIL.__version__)",
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "unknown")

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
        self.assertRegex(result["published_at_display"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_format_published_at_converts_to_local_timezone(self):
        display = _format_published_at(
            "2026-03-15T07:54:49Z",
            timezone(timedelta(hours=8)),
        )
        self.assertEqual(display, "2026-03-15 15:54:49")

    def test_check_latest_release_falls_back_to_web_when_api_403(self):
        fallback_payload = {
            "repo": "owner/repo",
            "current_version": "0.3.1",
            "latest_version": "0.3.2",
            "is_update_available": True,
            "release_name": "v0.3.2",
            "published_at": "2026-03-15T00:00:00Z",
            "body": "web fallback body",
            "html_url": "https://github.com/owner/repo/releases/tag/v0.3.2",
            "asset_name": "Literature-management-tool-v0.3.2-Setup.exe",
            "asset_url": "https://github.com/owner/repo/releases/download/v0.3.2/Literature-management-tool-v0.3.2-Setup.exe",
            "update_lookup_source": "web",
        }
        api_403 = error.HTTPError(
            url="https://api.github.com/repos/owner/repo/releases/latest",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        try:
            with mock.patch(
                "literature_manager.update_service._get_json",
                side_effect=api_403,
            ), mock.patch(
                "literature_manager.update_service._check_latest_release_via_web",
                return_value=fallback_payload,
            ) as web_fallback_mock:
                result = check_latest_release("owner/repo", "0.3.1")
        finally:
            api_403.close()
        web_fallback_mock.assert_called_once_with("owner/repo", "0.3.1")
        self.assertEqual(result["latest_version"], "0.3.2")
        self.assertEqual(result["update_lookup_source"], "web")
        self.assertEqual(result["update_lookup_notice"], "已通过备用通道获取到最新版本信息。")

    def test_build_fallback_notice_for_latest_version(self):
        self.assertEqual(_build_fallback_notice(False), "已通过备用通道检查到最新版本。")

    def test_check_latest_release_keeps_http_error_when_web_fallback_fails(self):
        api_403 = error.HTTPError(
            url="https://api.github.com/repos/owner/repo/releases/latest",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        try:
            with mock.patch(
                "literature_manager.update_service._get_json",
                side_effect=api_403,
            ), mock.patch(
                "literature_manager.update_service._check_latest_release_via_web",
                side_effect=error.URLError("offline"),
            ):
                with self.assertRaisesRegex(ValueError, "HTTP 403"):
                    check_latest_release("owner/repo", "0.3.1")
        finally:
            api_403.close()

    def test_check_latest_release_keeps_url_error_when_web_fallback_fails(self):
        fallback_500 = error.HTTPError(
            url="https://github.com/owner/repo/releases/latest",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )
        with mock.patch(
            "literature_manager.update_service._get_json",
            side_effect=error.URLError("temporary outage"),
        ), mock.patch(
            "literature_manager.update_service._check_latest_release_via_web",
            side_effect=fallback_500,
        ):
            with self.assertRaisesRegex(ValueError, "temporary outage"):
                check_latest_release("owner/repo", "0.3.1")
        fallback_500.close()


if __name__ == "__main__":
    unittest.main()
