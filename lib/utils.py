"""Utility functions for the standalone EPUB translator."""

import re
import hashlib
from html import escape as html_escape


# XHTML namespace
NS = {'x': 'http://www.w3.org/1999/xhtml'}


def uid(*args):
    """Generate an MD5 hash from the given arguments."""
    md5 = hashlib.md5()
    for arg in args:
        md5.update(arg if isinstance(arg, bytes) else arg.encode('utf-8'))
    return md5.hexdigest()


def trim(text):
    """Clean and normalize whitespace in text."""
    # Replace non-breaking spaces with regular spaces
    text = re.sub(u'\u00a0|\u3000', ' ', text)
    # Remove zero-width spaces and BOM
    text = re.sub(u'\u200b|\ufeff', '', text)
    # Combine multiple whitespace into single space
    text = re.sub(r'\s+', ' ', text)
    # Remove non-printable characters
    text = re.sub(r'(?![\n\r\t])[\x00-\x1f\x7f-\xa0\xad]', '', text)
    return text.strip()


def estimate_token_count(text):
    """Estimate token count without requiring a model-specific tokenizer.

    This intentionally favors a simple, dependency-free heuristic. CJK
    characters are counted individually, Latin words are counted as one token,
    and punctuation/non-space symbols are counted separately. It is not meant to
    predict billing exactly; it gives merge mode a model-relevant budget that is
    more stable than raw character length.
    """
    if not text:
        return 0
    tokens = re.findall(
        r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]|'
        r'[A-Za-z0-9]+(?:[\'-][A-Za-z0-9]+)*|'
        r'[^\s]',
        text,
    )
    return len(tokens)


def xml_escape(text):
    """Escape XML special characters."""
    return html_escape(text, quote=False)


def sorted_mixed_keys(s):
    """Sort key for mixed alphanumeric strings."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r'(\d+)', s)]


def css_to_xpath_simple(tag):
    """Convert a simple CSS tag selector to XPath with XHTML namespace.

    This is a simplified version that handles the common case of tag name
    selectors (p, h1, pre, etc.) without requiring the full cssselect library.
    """
    tag = tag.strip()
    # Simple tag name selector
    if re.match(r'^[A-Za-z][\w-]*$', tag):
        return (f'(self::x:{tag} or self::*[local-name()="{tag}"])')
    # Class selector: tag.class or .class
    m = re.match(r'^([A-Za-z][\w-]*)\.([A-Za-z][\w-]*)$', tag)
    if m:
        t, cls = m.groups()
        return (f'self::x:{t}[contains(@class, "{cls}")]')
    m = re.match(r'^\.([A-Za-z][\w-]*)$', tag)
    if m:
        cls = m.group(1)
        return f'self::*[contains(@class, "{cls}")]'
    # ID selector: tag#id or #id
    m = re.match(r'^([A-Za-z][\w-]*)#([A-Za-z][\w-]*)$', tag)
    if m:
        t, eid = m.groups()
        return f'self::x:{t}[@id="{eid}"]'
    m = re.match(r'^#([A-Za-z][\w-]*)$', tag)
    if m:
        eid = m.group(1)
        return f'self::*[@id="{eid}"]'
    return None


def css_to_xpath(selectors):
    """Convert a list of CSS selectors to XPath patterns."""
    patterns = []
    for sel in selectors:
        xpath = css_to_xpath_simple(sel)
        if xpath:
            patterns.append(xpath)
    return patterns


def create_xpath(selectors):
    """Create a combined XPath expression from CSS selectors."""
    if isinstance(selectors, str):
        selectors = (selectors,)
    patterns = css_to_xpath(selectors)
    if patterns:
        return './/*[%s]' % ' or '.join(patterns)
    return None
