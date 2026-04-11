import pathlib
import subprocess
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


class EnginesImportTest(unittest.TestCase):
    def test_target_language_code_maps_simplified_chinese(self):
        from epub_translator import get_target_language_code

        self.assertEqual(
            get_target_language_code("Chinese (Simplified)"),
            "zh-CN",
        )

    def test_google_free_engine_can_be_constructed(self):
        from engines import get_engine

        engine = get_engine(
            "google_free",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        self.assertEqual(engine.name, "google_free")
        self.assertEqual(engine.target_lang, "Chinese (Simplified)")

    def test_google_free_translate_uses_only_configured_key(self):
        from engines import get_engine

        engine = get_engine(
            "google_free",
            api_key="test-key",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        def fake_request(url, data=None, headers=None, method=None, raw=False):
            self.assertEqual(url, "https://translate-pa.googleapis.com/v1/translate")
            self.assertEqual(method, "GET")
            self.assertEqual(data["key"], "test-key")
            return '{"translation": "你好"}'

        with mock.patch.object(engine, "_make_request", side_effect=fake_request):
            self.assertEqual(engine.translate("Hello"), "你好")

    def test_google_free_translate_omits_key_when_not_configured(self):
        from engines import get_engine

        engine = get_engine(
            "google_free",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        def fake_request(url, data=None, headers=None, method=None, raw=False):
            self.assertNotIn("key", data)
            return '{"translation": "你好"}'

        with mock.patch.object(engine, "_make_request", side_effect=fake_request):
            self.assertEqual(engine.translate("Hello"), "你好")

    def test_google_free_html_translate_uses_only_configured_key(self):
        from engines import get_engine

        engine = get_engine(
            "google_free_html",
            api_key="test-key",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        def fake_request(url, data=None, headers=None, method=None, raw=False):
            self.assertEqual(
                url, "https://translate-pa.googleapis.com/v1/translateHtml"
            )
            self.assertEqual(headers["X-Goog-Api-Key"], "test-key")
            return '[["你好"]]'

        with mock.patch.object(engine, "_make_request", side_effect=fake_request):
            self.assertEqual(engine.translate("Hello"), "你好")

    def test_google_free_html_translate_omits_key_when_not_configured(self):
        from engines import get_engine

        engine = get_engine(
            "google_free_html",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        def fake_request(url, data=None, headers=None, method=None, raw=False):
            self.assertNotIn("X-Goog-Api-Key", headers)
            return '[["你好"]]'

        with mock.patch.object(engine, "_make_request", side_effect=fake_request):
            self.assertEqual(engine.translate("Hello"), "你好")

    def test_codex_engine_can_be_constructed(self):
        from engines import get_engine

        engine = get_engine(
            "codex",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        self.assertEqual(engine.name, "codex")
        self.assertEqual(engine.target_lang, "Chinese (Simplified)")

    def test_argos_local_engine_can_be_constructed(self):
        from engines import get_engine

        engine = get_engine(
            "argos_local",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        self.assertEqual(engine.name, "argos_local")
        self.assertEqual(engine.target_lang, "Chinese (Simplified)")
        self.assertTrue(engine.placeholder[0].startswith('<m id="'))

    def test_codex_engine_translate_uses_codex_exec(self):
        from engines import get_engine

        engine = get_engine(
            "codex",
            source_lang="English",
            target_lang="Chinese (Simplified)",
        )

        def fake_run(cmd, input=None, capture_output=None, text=None,
                     timeout=None, check=None):
            self.assertEqual(cmd[0], "codex")
            self.assertIn("exec", cmd)
            self.assertIn("--output-last-message", cmd)
            self.assertIn("-s", cmd)
            self.assertIn("read-only", cmd)
            self.assertIn("-c", cmd)
            self.assertIn('model_reasoning_effort="high"', cmd)
            self.assertIn("Hello", input)

            output_path = pathlib.Path(
                cmd[cmd.index("--output-last-message") + 1]
            )
            self.assertEqual(
                pathlib.Path(cmd[cmd.index("-C") + 1]),
                output_path.parent,
            )
            output_path.write_text("你好", encoding="utf-8")

            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("engines.subprocess.run", side_effect=fake_run):
            self.assertEqual(engine.translate("Hello"), "你好")

    def test_cli_help_lists_argos_local_engine(self):
        result = subprocess.run(
            [sys.executable, "epub_translator.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("argos_local", result.stdout)
        self.assertIn("codex", result.stdout)
