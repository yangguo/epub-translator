"""SQLite-based translation cache for persistence and resume support."""

import os
import re
import json
import sqlite3


class Paragraph:
    """A single translatable unit."""

    def __init__(self, id, md5, raw, original, ignored=False,
                 attributes=None, page=None, translation=None,
                 engine_name=None, target_lang=None):
        self.id = id
        self.md5 = md5
        self.raw = raw
        self.original = original
        self.ignored = ignored
        self.attributes = attributes
        self.page = page
        self.translation = translation
        self.engine_name = engine_name
        self.target_lang = target_lang

        self.row = -1
        self.is_cache = False
        self.error = None

    def do_alignment(self, separator):
        """Add line spacing to translation to fix misalignment."""
        if self.translation is None:
            return
        pattern = re.compile(separator)
        count_original = len(pattern.split(self.original.strip()))
        count_translation = len(pattern.split(self.translation.strip()))
        if count_original == count_translation:
            return
        single_sep = separator[0]
        lines = self.translation.split(single_sep)
        processed = []
        for i, line in enumerate(lines):
            processed.append(line)
            if (line.strip() and i + 1 < len(lines) and
                    lines[i + 1].strip()):
                processed.append('')
        self.translation = single_sep.join(processed)


class TranslationCache:
    """SQLite-backed translation cache.

    Each unique combination of (input file, engine, target language,
    merge length) gets its own cache database. This allows resuming
    interrupted translations without re-translating cached paragraphs.
    """

    def __init__(self, identity, cache_dir, enabled=True):
        self.identity = identity
        self.enabled = enabled
        self.cache_dir = cache_dir

        if enabled:
            os.makedirs(cache_dir, exist_ok=True)
            self.file_path = os.path.join(cache_dir, f'{identity}.db')
        else:
            self.file_path = ':memory:'

        self.connection = sqlite3.connect(
            self.file_path, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self.cursor.execute(
            'CREATE TABLE IF NOT EXISTS cache('
            'id UNIQUE, md5 UNIQUE, raw, original, ignored, '
            'attributes DEFAULT NULL, page DEFAULT NULL, '
            'translation DEFAULT NULL, engine_name DEFAULT NULL, '
            'target_lang DEFAULT NULL)')
        self.cursor.execute(
            'CREATE TABLE IF NOT EXISTS info(key UNIQUE, value)')
        self.connection.commit()

    def set_info(self, key, value):
        self.cursor.execute(
            'INSERT INTO info VALUES (?1, ?2) '
            'ON CONFLICT (KEY) DO UPDATE SET value=excluded.value',
            (key, value))
        self.connection.commit()

    def get_info(self, key):
        resource = self.cursor.execute(
            'SELECT value FROM info WHERE key=?', (key,))
        result = resource.fetchone()
        return result[0] if result else None

    def save(self, original_group):
        """Save original paragraphs to cache (skip if already exists)."""
        for unit in original_group:
            self._add(*unit)
        self.connection.commit()

    def _add(self, id, md5, raw, original, ignored=False,
             attributes=None, page=None):
        self.cursor.execute(
            'INSERT INTO cache VALUES ('
            '?1, ?2, ?3, ?4, ?5, ?6, ?7, NULL, NULL, NULL'
            ') ON CONFLICT DO NOTHING',
            (id, md5, raw, original, ignored, attributes, page))

    def all_paragraphs(self):
        """Get all non-ignored paragraphs."""
        resource = self.cursor.execute(
            'SELECT * FROM cache WHERE NOT ignored ORDER BY id')
        return [Paragraph(*row) for row in resource.fetchall()]

    def update_paragraph(self, paragraph):
        """Save a paragraph's translation to the cache."""
        self.cursor.execute(
            'UPDATE cache SET translation=?, engine_name=?, target_lang=? '
            'WHERE id=?',
            (paragraph.translation, paragraph.engine_name,
             paragraph.target_lang, paragraph.id))
        self.connection.commit()

    def close(self):
        self.cursor.close()
        self.connection.commit()
        self.connection.close()
