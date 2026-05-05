"""EPUB validation report generation."""

from dataclasses import dataclass, field
import hashlib
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Optional

from lxml import etree

from .signatures import diff_signatures, parse_xml, structure_signature


CONTAINER_PATH = "META-INF/container.xml"
HTML_EXTENSIONS = (".xhtml", ".html", ".htm", ".xml", ".xht")
OPF_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
PLACEHOLDER_PATTERN = re.compile(
    rb"({{\s*id\s*_[^}]+}})|(<x\s+id\s*=)", re.IGNORECASE)


@dataclass
class ValidationFinding:
    """A validation problem or review item."""

    category: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Structured validation result."""

    source_path: str
    output_path: str
    status: str
    summary: dict
    findings: list[ValidationFinding] = field(default_factory=list)

    def findings_for(self, category):
        """Return findings for a report category."""
        return [
            finding for finding in self.findings
            if finding.category == category
        ]


@dataclass
class _PackageInfo:
    names: list[str]
    file_names: set[str]
    opf_path: Optional[str] = None
    language: Optional[str] = None
    content_docs: set[str] = field(default_factory=set)
    control_files: set[str] = field(default_factory=set)
    package_errors: list[str] = field(default_factory=list)


def _zip_file_names(zf):
    return {name for name in zf.namelist() if not name.endswith("/")}


def _normalize_href(base_path, href):
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), href))


def _is_html_item(href, media_type):
    href_lower = href.lower()
    return href_lower.endswith(HTML_EXTENSIONS) or "html" in media_type


def _parse_package(zf):
    names = zf.namelist()
    info = _PackageInfo(names=names, file_names=_zip_file_names(zf))
    info.control_files.update({"mimetype", CONTAINER_PATH})

    try:
        container = etree.fromstring(zf.read(CONTAINER_PATH))
        rootfile = container.find(".//c:rootfile", namespaces=OPF_NS)
        if rootfile is None or not rootfile.get("full-path"):
            info.package_errors.append("Cannot find rootfile in container.xml")
            return info
        info.opf_path = rootfile.get("full-path")
        info.control_files.add(info.opf_path)
    except Exception as exc:
        info.package_errors.append(f"Cannot parse container.xml: {exc}")
        return info

    try:
        opf_root = etree.fromstring(zf.read(info.opf_path))
    except Exception as exc:
        info.package_errors.append(f"Cannot parse OPF {info.opf_path}: {exc}")
        return info

    lang_el = opf_root.find(".//dc:language", namespaces=OPF_NS)
    if lang_el is not None:
        info.language = lang_el.text

    manifest = opf_root.find(".//opf:manifest", namespaces=OPF_NS)
    if manifest is None:
        info.package_errors.append(f"Cannot find manifest in {info.opf_path}")
        return info

    for item in manifest.findall("opf:item", namespaces=OPF_NS):
        href = item.get("href", "")
        media_type = item.get("media-type", "")
        if href and _is_html_item(href, media_type):
            info.content_docs.add(_normalize_href(info.opf_path, href))

    return info


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _zip_status(path):
    result = {
        "is_zip": False,
        "testzip": None,
        "mimetype_first": False,
        "mimetype_stored": False,
    }
    if not zipfile.is_zipfile(path):
        return result
    result["is_zip"] = True
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        result["testzip"] = zf.testzip()
        if names:
            result["mimetype_first"] = names[0] == "mimetype"
            if names[0] == "mimetype":
                result["mimetype_stored"] = (
                    zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED)
    return result


def _add_zip_findings(findings, output_zip_status):
    if not output_zip_status["is_zip"]:
        findings.append(ValidationFinding(
            "archive", "fail", "Output is not a readable ZIP/EPUB archive"))
        return
    if output_zip_status["testzip"] is not None:
        findings.append(ValidationFinding(
            "archive", "fail", "ZIP integrity check failed",
            [str(output_zip_status["testzip"])]))
    if not output_zip_status["mimetype_first"]:
        findings.append(ValidationFinding(
            "archive", "fail", "EPUB mimetype entry is not first"))
    if not output_zip_status["mimetype_stored"]:
        findings.append(ValidationFinding(
            "archive", "fail", "EPUB mimetype entry is compressed"))


def _resource_entries(package_info):
    return (
        package_info.file_names
        - package_info.content_docs
        - package_info.control_files
    )


def _add_resource_findings(findings, source_zf, output_zf, source_info,
                           output_info):
    source_resources = _resource_entries(source_info)
    output_resources = _resource_entries(output_info)

    missing = sorted(source_resources - output_info.file_names)
    added = sorted(output_resources - source_info.file_names)
    common = sorted(source_resources & output_resources)
    changed = [
        name for name in common
        if _sha256(source_zf.read(name)) != _sha256(output_zf.read(name))
    ]

    if missing:
        findings.append(ValidationFinding(
            "resources", "fail", "Non-HTML resources missing from output",
            missing))
    if changed:
        findings.append(ValidationFinding(
            "resources", "fail", "Non-HTML resources changed unexpectedly",
            changed))
    if added:
        findings.append(ValidationFinding(
            "resources", "warning", "Output contains extra non-HTML resources",
            added))

    return missing, changed, added


def _add_package_findings(findings, label, package_info):
    for error in package_info.package_errors:
        findings.append(ValidationFinding(
            "package", "fail", f"{label} package error", [error]))


def _format_signature_diff(page, diff):
    parts = [
        f"{tag}: {source_count} -> {output_count}"
        for tag, (source_count, output_count) in diff.items()
    ]
    return f"{page}: {', '.join(parts)}"


def _add_html_findings(findings, source_zf, output_zf, source_info,
                       output_info):
    missing_docs = sorted(source_info.content_docs - output_info.file_names)
    added_docs = sorted(output_info.content_docs - source_info.file_names)
    parse_errors = []
    structure_diffs = []
    placeholder_leaks = []

    for name in sorted(source_info.content_docs & output_info.file_names):
        try:
            parse_xml(source_zf.read(name))
        except Exception as exc:
            parse_errors.append(f"{name} in source: {exc}")
            continue
        output_data = output_zf.read(name)
        try:
            parse_xml(output_data)
        except Exception as exc:
            parse_errors.append(f"{name} in output: {exc}")
            continue
        if PLACEHOLDER_PATTERN.search(output_data):
            placeholder_leaks.append(name)
        signature_diff = diff_signatures(
            structure_signature(source_zf.read(name)),
            structure_signature(output_data),
        )
        if signature_diff:
            structure_diffs.append(
                _format_signature_diff(name, signature_diff))

    if missing_docs:
        findings.append(ValidationFinding(
            "html", "fail", "HTML content documents missing from output",
            missing_docs))
    if added_docs:
        findings.append(ValidationFinding(
            "html", "warning", "Output contains extra HTML content documents",
            added_docs))
    if parse_errors:
        findings.append(ValidationFinding(
            "html", "fail", "HTML/XML parsing failed", parse_errors))
    if placeholder_leaks:
        findings.append(ValidationFinding(
            "placeholders", "fail", "Translation placeholders leaked",
            placeholder_leaks))
    if structure_diffs:
        findings.append(ValidationFinding(
            "structure", "warning", "HTML structure changed",
            structure_diffs))

    return {
        "missing_html_docs": len(missing_docs),
        "added_html_docs": len(added_docs),
        "html_parse_errors": len(parse_errors),
        "placeholder_leaks": len(placeholder_leaks),
        "structure_differences": len(structure_diffs),
    }


def _overall_status(findings):
    if any(finding.status == "fail" for finding in findings):
        return "fail"
    if any(finding.status == "warning" for finding in findings):
        return "review"
    return "pass"


def validate_epub_output(source_path, output_path):
    """Validate a translated EPUB against its source EPUB."""
    source_path = Path(source_path)
    output_path = Path(output_path)
    findings = []
    source_zip_status = _zip_status(source_path)
    output_zip_status = _zip_status(output_path)

    if not source_zip_status["is_zip"]:
        findings.append(ValidationFinding(
            "archive", "fail", "Source is not a readable ZIP/EPUB archive"))
    _add_zip_findings(findings, output_zip_status)

    summary = {
        "source_entries": 0,
        "output_entries": 0,
        "source_files": 0,
        "output_files": 0,
        "source_html_docs": 0,
        "output_html_docs": 0,
        "missing_resources": 0,
        "changed_non_html_resources": 0,
        "added_resources": 0,
        "missing_html_docs": 0,
        "added_html_docs": 0,
        "html_parse_errors": 0,
        "placeholder_leaks": 0,
        "structure_differences": 0,
        "source_language": None,
        "output_language": None,
        "mimetype_first": output_zip_status["mimetype_first"],
        "mimetype_stored": output_zip_status["mimetype_stored"],
    }

    if findings:
        return ValidationResult(
            str(source_path), str(output_path), _overall_status(findings),
            summary, findings)

    with zipfile.ZipFile(source_path) as source_zf, zipfile.ZipFile(output_path) as output_zf:
        source_info = _parse_package(source_zf)
        output_info = _parse_package(output_zf)
        _add_package_findings(findings, "Source", source_info)
        _add_package_findings(findings, "Output", output_info)

        summary.update({
            "source_entries": len(source_info.names),
            "output_entries": len(output_info.names),
            "source_files": len(source_info.file_names),
            "output_files": len(output_info.file_names),
            "source_html_docs": len(source_info.content_docs),
            "output_html_docs": len(output_info.content_docs),
            "source_language": source_info.language,
            "output_language": output_info.language,
        })

        if not source_info.package_errors and not output_info.package_errors:
            missing, changed, added = _add_resource_findings(
                findings, source_zf, output_zf, source_info, output_info)
            summary["missing_resources"] = len(missing)
            summary["changed_non_html_resources"] = len(changed)
            summary["added_resources"] = len(added)
            summary.update(_add_html_findings(
                findings, source_zf, output_zf, source_info, output_info))

    return ValidationResult(
        str(source_path), str(output_path), _overall_status(findings),
        summary, findings)


def _status_label(status):
    return status.upper()


def _heading_for_category(category):
    headings = {
        "archive": "Archive",
        "package": "Package",
        "resources": "Resource Differences",
        "html": "HTML Documents",
        "placeholders": "Placeholder Leaks",
        "structure": "Structure Differences",
    }
    return headings.get(category, category.title())


def render_markdown_report(result):
    """Render a validation result as Markdown."""
    lines = [
        "# EPUB Format Validation Report",
        "",
        f"- Source: `{result.source_path}`",
        f"- Output: `{result.output_path}`",
        f"- Overall Status: {_status_label(result.status)}",
        "",
        "## Summary",
    ]
    for key in sorted(result.summary):
        value = result.summary[key]
        lines.append(f"- {key}: {value}")

    if not result.findings:
        lines.extend(["", "## Findings", "- PASS: no validation findings"])
        return "\n".join(lines) + "\n"

    categories = []
    for finding in result.findings:
        if finding.category not in categories:
            categories.append(finding.category)

    for category in categories:
        lines.extend(["", f"## {_heading_for_category(category)}"])
        for finding in result.findings_for(category):
            lines.append(
                f"- {_status_label(finding.status)}: {finding.summary}")
            for detail in finding.details:
                lines.append(f"  - `{detail}`")

    return "\n".join(lines) + "\n"


def write_markdown_report(result, report_path):
    """Write a Markdown validation report and return its path."""
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown_report(result), encoding="utf-8")
    return report_path
