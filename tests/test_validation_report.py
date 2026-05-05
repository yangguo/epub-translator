import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from lib.validation.epub_report import (
    render_markdown_report,
    validate_epub_output,
    write_markdown_report,
)


XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
  <head><title>Sample</title><link rel="stylesheet" href="styles/main.css"/></head>
  <body>
    <h1>Sample</h1>
    <p><a href="https://example.com">Read more</a></p>
    <figure><img src="images/cover.png" alt="cover"/></figure>
  </body>
</html>
"""


def write_epub(path, html=XHTML, include_directories=False, include_css=True):
    manifest_items = [
        '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
    ]
    if include_css:
        manifest_items.append(
            '<item id="css" href="styles/main.css" media-type="text/css"/>'
        )
    manifest_items.append(
        '<item id="cover" href="images/cover.png" media-type="image/png"/>'
    )
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Sample</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>{''.join(manifest_items)}</manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        if include_directories:
            for name in ("META-INF/", "OEBPS/", "OEBPS/styles/", "OEBPS/images/"):
                zf.writestr(name, "")
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/chapter.xhtml", html)
        if include_css:
            zf.writestr("OEBPS/styles/main.css", "body { color: #111; }")
        zf.writestr("OEBPS/images/cover.png", b"fake png bytes")


class ValidationReportTest(unittest.TestCase):
    def test_directory_entries_missing_from_output_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.epub"
            output = Path(tmp) / "output.epub"
            write_epub(source, include_directories=True)
            write_epub(output, include_directories=False)

            result = validate_epub_output(source, output)

            self.assertEqual(result.status, "pass")
            self.assertEqual(result.summary["missing_resources"], 0)
            self.assertEqual(result.summary["changed_non_html_resources"], 0)
            self.assertFalse(result.findings_for("resources"))

    def test_missing_real_non_html_resource_fails_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.epub"
            output = Path(tmp) / "output.epub"
            write_epub(source, include_css=True)
            write_epub(output, include_css=False)

            result = validate_epub_output(source, output)

            self.assertEqual(result.status, "fail")
            findings = result.findings_for("resources")
            self.assertEqual(findings[0].status, "fail")
            self.assertIn("OEBPS/styles/main.css", findings[0].details)

    def test_structure_changes_are_reported_for_review(self):
        changed_html = XHTML.replace(
            '<p><a href="https://example.com">Read more</a></p>',
            "<p>Read more</p>",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.epub"
            output = Path(tmp) / "output.epub"
            write_epub(source)
            write_epub(output, html=changed_html)

            result = validate_epub_output(source, output)
            markdown = render_markdown_report(result)

            self.assertEqual(result.status, "review")
            self.assertEqual(result.summary["structure_differences"], 1)
            self.assertIn("Structure Differences", markdown)
            self.assertIn("OEBPS/chapter.xhtml", markdown)
            self.assertIn("a: 1 -> 0", markdown)

    def test_placeholder_leaks_fail_validation(self):
        changed_html = XHTML.replace("Read more", "{{id_000001}}")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.epub"
            output = Path(tmp) / "output.epub"
            write_epub(source)
            write_epub(output, html=changed_html)

            result = validate_epub_output(source, output)

            self.assertEqual(result.status, "fail")
            findings = result.findings_for("placeholders")
            self.assertEqual(findings[0].status, "fail")
            self.assertIn("OEBPS/chapter.xhtml", findings[0].details)

    def test_markdown_report_can_be_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.epub"
            output = Path(tmp) / "output.epub"
            report = Path(tmp) / "report.md"
            write_epub(source)
            write_epub(output)

            result = validate_epub_output(source, output)
            written = write_markdown_report(result, report)

            self.assertEqual(written, report)
            text = report.read_text(encoding="utf-8")
            self.assertIn("# EPUB Format Validation Report", text)
            self.assertIn("Overall Status: PASS", text)


class ValidationCliTest(unittest.TestCase):
    def test_default_validation_report_path_replaces_epub_suffix(self):
        from epub_translator import get_default_validation_report_path

        self.assertEqual(
            get_default_validation_report_path("/tmp/book.zh-CN.epub"),
            "/tmp/book.zh-CN.validation.md",
        )

    def test_validation_exception_does_not_escape_cli_helper(self):
        from epub_translator import run_output_validation

        stdout = StringIO()
        with mock.patch(
            "lib.validation.epub_report.validate_epub_output",
            side_effect=OSError("permission denied"),
        ):
            with redirect_stdout(stdout):
                result = run_output_validation(
                    "source.epub", "output.epub", "report.md")

        self.assertIsNone(result)
        self.assertIn("Warning: Validation could not be completed", stdout.getvalue())
        self.assertIn("permission denied", stdout.getvalue())

    def test_report_write_exception_does_not_escape_cli_helper(self):
        from epub_translator import run_output_validation

        validation_result = mock.Mock(status="pass")
        stdout = StringIO()
        with mock.patch(
            "lib.validation.epub_report.validate_epub_output",
            return_value=validation_result,
        ):
            with mock.patch(
                "lib.validation.epub_report.write_markdown_report",
                side_effect=OSError("disk full"),
            ):
                with redirect_stdout(stdout):
                    result = run_output_validation(
                        "source.epub", "output.epub", "report.md")

        self.assertIsNone(result)
        self.assertIn("Warning: Validation could not be completed", stdout.getvalue())
        self.assertIn("disk full", stdout.getvalue())

    def test_cli_help_lists_validation_flags(self):
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "epub_translator.py", "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("--validate-output", result.stdout)
        self.assertIn("--validation-report", result.stdout)


if __name__ == "__main__":
    unittest.main()
