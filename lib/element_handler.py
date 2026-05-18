"""Element handler for preparing originals and applying translations.

Manages the relationship between PageElements and the translation cache,
including optional paragraph merging for reduced API calls.
"""

import re
from typing import Any

from .utils import uid, create_xpath, estimate_token_count


class ElementHandler:
    """Prepares elements for translation and applies translations back.

    In merge mode, consecutive paragraphs are concatenated with a separator
    to reduce the number of API calls. After translation, the merged result
    is split back and paired with individual elements.
    """

    def __init__(self, placeholder, separator, position,
                 merge_enabled=False, merge_length=1800, merge_tokens=None,
                 original_color=None, translation_color=None,
                 target_direction=None, column_gap=None,
                 remove_rules=None, reserve_rules=None):
        self.placeholder = placeholder
        self.separator = separator
        self.position = position
        self.merge_enabled = merge_enabled
        self.merge_length = merge_length
        self.merge_tokens = merge_tokens
        self.original_color = original_color
        self.translation_color = translation_color
        self.target_direction = target_direction
        self.column_gap = column_gap

        # Build remove/reserve XPath patterns
        default_remove = ('rt', 'rp')
        remove_selectors = default_remove + tuple(remove_rules or [])
        self.remove_pattern = create_xpath(remove_selectors)

        default_reserve = (
            'img', 'code', 'br', 'hr', 'sub', 'sup', 'kbd', 'abbr',
            'wbr', 'var', 'canvas', 'svg', 'script', 'style', 'math')
        reserve_selectors = default_reserve + tuple(reserve_rules or [])
        self.reserve_pattern = create_xpath(reserve_selectors)

        self.elements = {}
        self.originals = []

    def prepare_original(self, elements):
        """Prepare elements for translation.

        Configure each element with placeholder, position, color settings,
        then extract content. Returns list of tuples for cache storage.

        If merge is enabled, consecutive small paragraphs are concatenated.
        """
        if self.merge_enabled:
            return self._prepare_merged(elements)
        return self._prepare_individual(elements)

    def _configure_element(self, element):
        """Apply all configuration to an element."""
        element.placeholder = self.placeholder
        element.position = self.position
        element.target_direction = self.target_direction
        element.translation_lang = None
        element.original_color = self.original_color
        element.translation_color = self.translation_color
        if self.column_gap is not None:
            element.column_gap = self.column_gap
        element.remove_pattern = self.remove_pattern
        element.reserve_pattern = self.reserve_pattern

    def _prepare_individual(self, elements):
        """Prepare individual (non-merged) elements."""
        count = 0
        for oid, element in enumerate(elements):
            self._configure_element(element)
            raw = element.get_raw()
            content = element.get_content()
            if content.strip() == '':
                element.set_ignored(True)
            md5 = uid('%s%s' % (oid, content))
            attrs = element.get_attributes()
            if not element.ignored:
                element._para_oid = oid
                self.elements[count] = element
                count += 1
            self.originals.append((
                oid, md5, raw, content, element.ignored, attrs,
                element.page_id))
        return self.originals

    def _prepare_merged(self, elements):
        """Prepare merged elements -- concatenate small paragraphs."""
        raw = ''
        txt = ''
        oid = 0
        for eid, element in enumerate(elements):
            self.elements[eid] = element
            self._configure_element(element)
            if element.ignored:
                continue
            code = element.get_raw()
            content = element.get_content()
            if content.strip() == '':
                element.set_ignored(True)
                continue
            content += self.separator
            if self._within_merge_budget(txt, content):
                raw += code + self.separator
                txt += content
                continue
            elif txt:
                md5 = uid('%s%s' % (oid, txt))
                self.originals.append((oid, md5, raw, txt, False))
                oid += 1
            raw = code
            txt = content
        if txt:
            md5 = uid('%s%s' % (oid, txt))
            self.originals.append((oid, md5, raw, txt, False))
        return self.originals

    def _within_merge_budget(self, current, addition):
        """Return True when adding content keeps the merged unit in budget."""
        candidate = current + addition
        if self.merge_tokens is not None:
            return estimate_token_count(candidate) < self.merge_tokens
        return len(candidate) < self.merge_length

    def prepare_translation(self, paragraphs):
        """Build translation mapping from paragraphs."""
        if self.merge_enabled:
            translations = {}
            idx = 0
            for paragraph in paragraphs:
                segments = self._align_paragraph(paragraph)
                for original, translation in segments:
                    translations[idx] = (original, translation)
                    idx += 1
            return translations
        return {p.id: p.translation for p in paragraphs}

    def _align_paragraph(self, paragraph):
        """Split merged translation back into individual segments."""
        # Clean up placeholders used as separators
        if paragraph.original[-2:] != self.separator:
            pattern = re.compile(
                r'\s*%s\s*' % self.placeholder[1].format(r'(0|[^0]\d*)'))
            paragraph.original = pattern.sub(
                self.separator, paragraph.original)
            if paragraph.translation is not None:
                paragraph.translation = pattern.sub(
                    self.separator, paragraph.translation)

        originals = paragraph.original.strip().split(self.separator)
        if paragraph.translation is None:
            return list(zip(originals, [None] * len(originals)))

        pattern = re.compile('%s+' % self.separator)
        translation = pattern.sub(self.separator, paragraph.translation)
        translations: list[Any] = translation.strip().split(self.separator)

        offset = len(originals) - len(translations)
        if offset > 0:
            if self.position in ('left', 'right'):
                translations += [None] * offset
            else:
                merged = '\n\n'.join(translations)
                translations = [None] * (len(originals) - 1)
                if self.position == 'above':
                    translations.insert(0, merged)
                else:
                    translations.append(merged)
        elif offset < 0:
            last_idx = len(originals) - 1
            translations = (translations[:last_idx] +
                            ['\n\n'.join(translations[last_idx:])])

        return list(zip(originals, translations))

    def add_translations(self, paragraphs):
        """Apply translations to all elements in the tree."""
        translations = self.prepare_translation(paragraphs)
        if self.merge_enabled:
            # Merged mode: translations indexed by sequential position
            merge_idx = 0
            for eid, element in self.elements.copy().items():
                if element.ignored:
                    element.add_translation()
                    continue
                entry = translations.get(merge_idx)
                merge_idx += 1
                if entry is None:
                    element.add_translation()
                    continue
                _original, translation = entry
                if translation is None:
                    element.add_translation()
                    continue
                element.add_translation(translation)
        else:
            for eid, element in self.elements.copy().items():
                if element.ignored:
                    element.add_translation()
                    continue
                oid = getattr(element, '_para_oid', None)
                translation = translations.get(oid) if oid is not None else None
                if translation is None:
                    element.add_translation()
                    continue
                element.add_translation(translation)
