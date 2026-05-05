"""Lightweight XHTML structure signatures for EPUB validation."""

from collections import Counter

from lxml import etree


STRUCTURE_TAGS = (
    "a",
    "img",
    "picture",
    "source",
    "h1",
    "h2",
    "h3",
    "div",
    "li",
    "table",
    "figure",
)


def parse_xml(data):
    """Parse XHTML bytes without network/entity expansion."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.fromstring(data, parser)


def structure_signature(data):
    """Count stable structural tags in an XHTML document."""
    root = parse_xml(data)
    counts = Counter()
    for element in root.iter():
        try:
            name = etree.QName(element).localname
        except ValueError:
            continue
        if name in STRUCTURE_TAGS:
            counts[name] += 1
    return {name: counts[name] for name in STRUCTURE_TAGS if counts[name]}


def diff_signatures(source_signature, output_signature):
    """Return tag count changes between two structure signatures."""
    diff = {}
    tags = set(source_signature) | set(output_signature)
    for tag in STRUCTURE_TAGS:
        if tag not in tags:
            continue
        source_count = source_signature.get(tag, 0)
        output_count = output_signature.get(tag, 0)
        if source_count != output_count:
            diff[tag] = (source_count, output_count)
    return diff
