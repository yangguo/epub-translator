"""Content extraction from EPUB XHTML documents.

This module implements the same extraction logic as the Calibre plugin:
- Identifies translatable text elements (paragraphs, headings, etc.)
- Filters out non-text content (punctuation-only, code blocks, etc.)
- Handles element priority rules and ignore rules
"""

import re
import copy
import json

from lxml import etree

from .utils import NS, trim, css_to_xpath, create_xpath, xml_escape


def get_string(element, remove_ns=False):
    """Get the string representation of an lxml element."""
    element.text = element.text or ''  # prevent auto-closing
    markup = trim(etree.tostring(
        element, encoding='utf-8', with_tail=False).decode('utf-8'))
    return re.sub(r'\sxmlns="[^"]+"', '', markup) if remove_ns else markup


def get_name(element):
    """Get the local name of an element (without namespace)."""
    return etree.QName(element).localname


class PageElement:
    """Represents a translatable element in an XHTML page.

    Mirrors the plugin's PageElement class with the same extraction
    and translation-merging logic.
    """

    format_elements = (
        'a', 'em', 'strong', 'small', 's', 'cite', 'q', 'time', 'samp',
        'i', 'b', 'u', 'mark', 'data', 'del', 'ins')

    def __init__(self, element, page_id=None, ignored=False):
        self.element = element
        self.page_id = page_id
        self.ignored = ignored

        self.placeholder = ()
        self.reserve_elements = []
        self.placeholder_replacements = []
        self.wrapper_placeholders = []
        self.column_gap = None

        self.position = None
        self.target_direction = None
        self.translation_lang = None
        self.original_color = None
        self.translation_color = None

        self.remove_pattern = None
        self.reserve_pattern = None

    def _element_copy(self):
        return copy.deepcopy(self.element)

    def set_ignored(self, ignored):
        self.ignored = ignored

    def get_raw(self):
        """Get the raw HTML markup of this element."""
        return get_string(self.element, True)

    def get_text(self):
        """Get the plain text content."""
        return trim(''.join(self.element.itertext()))

    def get_attributes(self):
        """Get element attributes as JSON string."""
        attributes = dict(self.element.attrib.items())
        return json.dumps(attributes) if attributes else None

    def _safe_remove(self, element, replacement=''):
        """Remove an element from the tree, preserving text/tail."""
        previous, parent = element.getprevious(), element.getparent()
        if previous is not None:
            previous.tail = (previous.tail or '') + replacement
            previous.tail += (element.tail or '')
        else:
            parent.text = (parent.text or '') + replacement
            parent.text += (element.tail or '')
        element.tail = None
        parent.remove(element)

    def _make_placeholder(self, marker):
        return self.placeholder[0].format(marker)

    def _marker_pattern(self, marker):
        pattern = []
        idx = 0
        while idx < len(marker):
            if marker[idx] == '0':
                while idx < len(marker) and marker[idx] == '0':
                    idx += 1
                pattern.append('0+')
                continue
            pattern.append(re.escape(marker[idx]))
            idx += 1
        return r'\s*'.join(pattern)

    def _placeholder_pattern(self, marker):
        return self.placeholder[1].format(self._marker_pattern(marker))

    def _register_placeholder(self, marker, markup):
        self.placeholder_replacements.append((marker, markup))
        return self._make_placeholder(marker)

    def _make_wrapper_placeholder(self, marker):
        return f'<x id="{marker}">'

    def _split_element_markup(self, element):
        wrapper = copy.deepcopy(element)
        wrapper.text = ''
        for child in list(wrapper):
            wrapper.remove(child)
        markup = get_string(wrapper, True)
        closing = f'</{get_name(element)}>'
        if markup.endswith(closing):
            opening = markup[:-len(closing)]
        else:
            opening = markup
        return opening, closing

    def _should_reserve_link_wrapper(self, element, reserved_ids):
        if get_name(element) != 'a' or len(element) != 1:
            return False
        if trim(element.text or '') != '':
            return False
        child = element[0]
        if get_name(child) not in ('sub', 'sup'):
            return False
        if trim(child.tail or '') != '':
            return False
        return id(child) in reserved_ids

    def _extract_content_from_element(
            self, element, removed_ids, reserved_ids, inside_wrapper=False):
        parts = [element.text or '']
        for child in element:
            child_id = id(child)
            if child_id in removed_ids:
                parts.append(child.tail or '')
                continue

            if (self._should_reserve_link_wrapper(child, reserved_ids) or
                    child_id in reserved_ids):
                marker = f'r{len(self.reserve_elements):05d}'
                self.reserve_elements.append(get_string(child, True))
                parts.append(
                    self._register_placeholder(marker, get_string(child, True)))
                parts.append(child.tail or '')
                continue

            if not inside_wrapper and get_name(child) in self.format_elements:
                marker = f'f{len(self.wrapper_placeholders):05d}9'
                opening, closing = self._split_element_markup(child)
                self.wrapper_placeholders.append((marker, opening, closing))
                parts.append(self._make_wrapper_placeholder(marker))
                parts.append(
                    self._extract_content_from_element(
                        child, removed_ids, reserved_ids,
                        inside_wrapper=True))
                parts.append('</x>')
                parts.append(child.tail or '')
                continue

            parts.append(
                self._extract_content_from_element(
                    child, removed_ids, reserved_ids,
                    inside_wrapper=inside_wrapper))
            parts.append(child.tail or '')
        return ''.join(parts)

    def get_content(self):
        """Extract translatable text with placeholders for reserved elements.

        This is the core extraction method. It:
        1. Removes noise elements (ruby annotations: rt, rp)
        2. Identifies elements to reserve (img, code, br, hr, sub, sup, etc.)
        3. Replaces reserved elements with placeholders like {{id_00000}}
        4. Returns the plain text with placeholders
        """
        element_copy = self._element_copy()
        self.reserve_elements = []
        self.placeholder_replacements = []
        self.wrapper_placeholders = []

        removed_ids = set()
        if self.remove_pattern is not None:
            removed_ids = {
                id(noise)
                for noise in element_copy.xpath(
                    self.remove_pattern, namespaces=NS)
            }

        reserved_ids = set()
        if self.reserve_pattern is not None:
            reserved_ids = {
                id(reserved)
                for reserved in element_copy.xpath(
                    self.reserve_pattern, namespaces=NS)
            }

        return trim(self._extract_content_from_element(
            element_copy, removed_ids, reserved_ids))

    def _polish_translation(self, translation):
        """Polish translation text: convert newlines to <br/>, limit repeats."""
        translation = translation.replace('\n', '<br/>')
        return re.sub(r'((\w)\2{3})\2*', r'\1', translation)

    def _create_new_element(self, name, content='', copy_attrs=True,
                            excluding_attrs=[]):
        """Create a new element with namespace support."""
        namespaces = ' '.join(
            'xmlns%s="%s"' % ('' if ns_name is None else ':' + ns_name, value)
            for ns_name, value in self.element.nsmap.items())
        new_element = etree.XML(
            '<{0} {1}>{2}</{0}>'.format(name, namespaces, trim(content)))
        if copy_attrs:
            for attr_name, value in self.element.items():
                if ((attr_name == 'id' and self.position != 'only') or
                        attr_name in excluding_attrs):
                    continue
                new_element.set(attr_name, value)
        new_element.set('dir', self.target_direction or 'auto')
        if self.translation_lang is not None:
            new_element.set('lang', self.translation_lang)
        if self.translation_color is not None:
            new_element.set('style', 'color:%s' % self.translation_color)
        return new_element

    def _create_table(self, translation=None):
        """Create a side-by-side table layout for left/right positioning."""
        original = self._element_copy()
        table = etree.XML(
            '<table xmlns="{}" width="100%"></table>'.format(NS['x']))
        tr = etree.SubElement(table, 'tr')
        td_left = etree.SubElement(tr, 'td', attrib={'valign': 'top'})
        td_middle = etree.SubElement(tr, 'td')
        td_right = etree.SubElement(tr, 'td', attrib={'valign': 'top'})

        if self.column_gap is None:
            td_left.set('width', '45%')
            td_middle.set('width', '10%')
            td_right.set('width', '45%')
        else:
            unit, value = self.column_gap
            if unit == 'percentage':
                width = '%s%%' % round((100 - value) / 2)
                td_left.set('width', width)
                td_middle.set('width', '%s%%' % value)
                td_right.set('width', width)
            else:
                td_left.set('width', '50%')
                td_middle.text = '\xa0' * value
                td_right.set('width', '50%')

        if self.position == 'left':
            if translation is not None:
                td_left.append(translation)
            td_right.append(original)
        elif self.position == 'right':
            td_left.append(original)
            if translation is not None:
                td_right.append(translation)
        return table

    def add_translation(self, translation=None):
        """Apply translation to the lxml tree in-place.

        This modifies the original XHTML element to include the translation,
        handling all position modes: only, below, above, left, right.
        """
        # Color the original text
        if self.original_color is not None:
            for el in self.element.iter():
                if el.text is not None or len(list(el)) > 0:
                    try:
                        el.set('style', 'color:%s' % self.original_color)
                    except TypeError:
                        pass

        if translation is None:
            if self.position in ('left', 'right'):
                self.element.addnext(self._create_table())
                self._safe_remove(self.element)
            return

        # Escape the translation and restore reserved elements
        translation = xml_escape(translation)
        for marker, reserved_el in self.placeholder_replacements:
            pattern = self._placeholder_pattern(marker)
            translation = re.sub(
                xml_escape(pattern), lambda _: reserved_el, translation)
        for marker, opening, closing in self.wrapper_placeholders:
            pattern = re.compile(
                r'&lt;\s*x\s+id\s*=\s*["\']?%s["\']?\s*&gt;(.*?)&lt;\s*/\s*x\s*&gt;'
                % self._marker_pattern(marker),
                re.DOTALL)
            translation = pattern.sub(
                lambda match: opening + match.group(1) + closing,
                translation)

        translation = self._polish_translation(translation)
        element_name = get_name(self.element)
        new_element = self._create_new_element(element_name, translation)

        # Handle table cell elements (li, th, td, caption)
        group_elements = ('li', 'th', 'td', 'caption')
        if element_name in group_elements:
            if self.position == 'only':
                self.element.addnext(new_element)
                self._safe_remove(self.element)
                return
            new_element = self._create_new_element(
                'span', translation, excluding_attrs=['class'])
            if self.position in ('left', 'above'):
                if self.element.text is not None:
                    if self.position == 'above':
                        br = etree.SubElement(self.element, 'br')
                        br.tail = self.element.text
                        self.element.insert(0, br)
                    else:
                        new_element.tail = ' ' + self.element.text
                    self.element.text = None
                self.element.insert(0, new_element)
            else:
                if self.position == 'below':
                    self.element.append(etree.SubElement(self.element, 'br'))
                else:
                    children = self.element.getchildren()
                    if len(children) > 0:
                        el = children[-1]
                        el.tail = (el.tail or '') + ' '
                    else:
                        self.element.text = (self.element.text or '') + ' '
                self.element.append(new_element)
            return

        is_text_element = element_name in self.format_elements

        # Left/right table layout
        if self.position in ('left', 'right') and not is_text_element:
            self.element.addnext(self._create_table(new_element))
            self._safe_remove(self.element)
            return

        # Line break alignment
        line_break_tag = '{%s}br' % NS['x']
        original_br_list = list(
            self.element.iterdescendants(line_break_tag))
        translation_br_list = list(
            new_element.iterchildren(line_break_tag))
        if (len(original_br_list) == len(translation_br_list) > 0 and
                self.position in ('below', 'above')):
            self._add_translation_for_line_breaks(
                new_element, original_br_list, translation_br_list)
            return

        parent_element = self.element.getparent()
        is_table_descendant = (
            parent_element is not None and
            get_name(parent_element) in group_elements)

        if self.position == 'only':
            self.element.addnext(new_element)
            self._safe_remove(self.element)
            return

        if self.position in ('left', 'above'):
            self.element.addprevious(new_element)
            if is_text_element and is_table_descendant:
                new_element.addnext(
                    etree.SubElement(self.element, 'br'))
            elif is_text_element:
                new_element.tail = ' '
        else:
            self.element.addnext(new_element)
            if is_text_element and is_table_descendant:
                self.element.addnext(
                    etree.SubElement(self.element, 'br'))
            elif is_text_element:
                if self.element.tail is not None:
                    new_element.tail = self.element.tail
                self.element.tail = ' '

    def _add_translation_for_line_breaks(
            self, new_element, original_br_list, translation_br_list):
        """Align translations with line breaks in the original."""
        text = new_element.text
        tail = None
        if self.position == 'below':
            for index, br in enumerate(original_br_list):
                translation_br = translation_br_list[index]
                wrapper = self._create_new_element(
                    'span', copy_attrs=False, excluding_attrs=['class'])
                for sibling in translation_br.itersiblings(preceding=True):
                    if get_name(sibling) == 'br':
                        break
                    wrapper.insert(0, sibling)
                wrapper.text = text if index == 0 else tail
                tail = translation_br.tail
                if wrapper.text or len(list(wrapper)) > 0:
                    new_br = etree.SubElement(self.element, 'br')
                    br.addprevious(new_br)
                    new_br.addnext(wrapper)
                if br == original_br_list[-1]:
                    if (translation_br.getnext() is None and
                            (tail is None or tail.strip() == '')):
                        continue
                    wrapper = self._create_new_element(
                        'span', copy_attrs=False, excluding_attrs=['class'])
                    for sibling in translation_br.itersiblings():
                        wrapper.append(sibling)
                    wrapper.text = tail
                    new_br = etree.SubElement(self.element, 'br')
                    self.element.append(new_br)
                    new_br.addnext(wrapper)
        else:
            for index, br in enumerate(original_br_list):
                translation_br = translation_br_list[index]
                wrapper = self._create_new_element(
                    'span', copy_attrs=False, excluding_attrs=['class'])
                for sibling in translation_br.itersiblings():
                    if get_name(sibling) == 'br':
                        break
                    wrapper.insert(0, sibling)
                wrapper.text = translation_br.tail
                if wrapper.text or len(list(wrapper)) > 0:
                    new_br = etree.SubElement(self.element, 'br')
                    new_br.tail = br.tail
                    br.tail = None
                    br.addnext(new_br)
                    new_br.addprevious(wrapper)
                if br == original_br_list[0]:
                    wrapper = self._create_new_element(
                        'span', copy_attrs=False, excluding_attrs=['class'])
                    if (translation_br.getprevious() is None and
                            (text is None or text.strip() == '')):
                        continue
                    for sibling in translation_br.itersiblings(
                            preceding=True):
                        wrapper.insert(0, sibling)
                    wrapper.text = new_element.text
                    new_br = etree.SubElement(self.element, 'br')
                    new_br.tail = self.element.text
                    self.element.text = None
                    self.element.insert(0, new_br)
                    new_br.addprevious(wrapper)


class Extraction:
    """Extract translatable elements from EPUB content documents.

    Implements the same recursive extraction algorithm as the plugin:
    - Priority elements (p, h1-h6, pre, blockquote) are extracted as-is
    - Inline-only containers are treated as single paragraphs
    - Code/pre blocks are ignored by default
    - Content is filtered by regex patterns
    """

    # Non-inline elements per HTML spec
    non_inline_elements = (
        'address', 'blockquote', 'dialog', 'div', 'figure', 'figcaption',
        'footer', 'header', 'legend', 'main', 'p', 'pre', 'search',
        'article', 'aside', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hgroup',
        'nav', 'section', 'dd', 'dl', 'dt', 'li', 'menu', 'ol', 'ul', 'table',
        'caption', 'colgroup', 'col', 'thead', 'tbody', 'tfoot', 'tr',
        'td', 'th')

    def __init__(self, pages, priority_rules=None, filter_rules=None,
                 ignore_rules=None):
        self.pages = pages
        self.priority_rules = priority_rules or []
        self.filter_rules = filter_rules or []
        self.ignore_rules = ignore_rules or []

        self.priority_patterns = []
        self.filter_patterns = []
        self.ignore_patterns = []

        self._load_priority_patterns()
        self._load_filter_patterns()
        self._load_ignore_patterns()

    def _load_priority_patterns(self):
        default = ['p', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                    'blockquote']
        self.priority_patterns = css_to_xpath(default + self.priority_rules)

    def _load_filter_patterns(self):
        default_rules = (
            r'^[-\d\s\.\'\\"''"",=~!@#$%^&º*|≈<>?/`—…+:–_(){}[\]]+$',)
        self.filter_patterns = [re.compile(rule) for rule in default_rules]
        for rule in self.filter_rules:
            self.filter_patterns.append(re.compile(rule))

    def _load_ignore_patterns(self):
        default = ['pre', 'code']
        self.ignore_patterns = css_to_xpath(default + self.ignore_rules)

    def get_elements(self):
        """Extract all translatable elements from content documents."""
        elements = []
        for page in self.pages:
            body = page.data.find('./x:body', namespaces=NS)
            if body is None:
                # Try without namespace
                body = page.data.find('.//body')
            if body is None:
                continue
            elements.extend(
                self._extract_elements(page.id, body, []))
        return list(filter(self._filter_content, elements))

    def _is_priority(self, element):
        for pattern in self.priority_patterns:
            if element.xpath(pattern, namespaces=NS):
                return True
        return False

    def _is_inline_only(self, element):
        """Check if element contains only inline elements."""
        return not any(
            get_name(descendant) in self.non_inline_elements
            for descendant in element.iterdescendants())

    def _need_ignore(self, element):
        for pattern in self.ignore_patterns:
            if element.xpath(pattern, namespaces=NS):
                return True
        return False

    def _extract_elements(self, page_id, root, elements=[]):
        """Recursively extract translatable elements from the tree."""
        if self._need_ignore(root):
            return []
        for element in root.findall('./*'):
            if self._need_ignore(element):
                elements.append(PageElement(element, page_id, True))
                continue
            element_has_content = False
            if (self._is_priority(element) or
                    self._is_inline_only(element) or
                    (element.text is not None and
                     trim(element.text) != '')):
                element_has_content = True
            else:
                children = element.findall('./*')
                if children and self._is_priority(element):
                    element_has_content = True
                else:
                    for child in children:
                        if (child.tail is not None and
                                trim(child.tail) != ''):
                            element_has_content = True
                            break
            if element_has_content:
                elements.append(PageElement(
                    element, page_id, self._need_ignore(element)))
            else:
                self._extract_elements(page_id, element, elements)
        return elements if elements else [
            PageElement(root, page_id, self._need_ignore(root))]

    def _filter_content(self, element):
        """Filter out elements with empty or non-translatable content."""
        content = element.get_text()
        if content == '':
            return False
        for entity in ('&lt;', '&gt;'):
            content = content.replace(entity, '')
        for pattern in self.filter_patterns:
            if pattern.search(content):
                element.set_ignored(True)
        return True
