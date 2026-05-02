#!/usr/bin/env python3
"""
Standalone EPUB Translator
Translates EPUB files from one language to another while preserving formatting.

Based on the logic of the Ebook-Translator-Calibre-Plugin by bookfere.

Usage:
    python epub_translator.py input.epub -s English -t "Chinese (Simplified)" \
        --engine codex --output translated.epub

Dependencies:
    pip install lxml requests
"""

import argparse
import sys
import os

from lib.epub import EpubFile
from lib.extraction import Extraction, PageElement
from lib.element_handler import ElementHandler
from lib.cache import TranslationCache, Paragraph
from lib.translator import Translation, Glossary
from engines import get_engine
from engines.languages import GOOGLE_LANGS, DEEPL_TARGET_LANGS


def parse_args():
    parser = argparse.ArgumentParser(
        description='Translate EPUB files while preserving formatting.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default Codex CLI engine (uses local Codex auth, no API key):
  python epub_translator.py book.epub -t "Chinese (Simplified)"

  # OpenAI ChatGPT:
  python epub_translator.py book.epub -t Japanese \\
      --engine chatgpt --api-key sk-xxx --model gpt-4o

  # Codex CLI (uses local Codex auth, no API key):
  python epub_translator.py book.epub -t "Chinese (Simplified)" \\
      --engine codex

  # Anthropic Claude:
  python epub_translator.py book.epub -t French \\
      --engine claude --api-key sk-ant-xxx

  # DeepL API:
  python epub_translator.py book.epub -t German \\
      --engine deepl --api-key xxx-xxx:fx

  # Google Free with bilingual output:
  python epub_translator.py book.epub -t Spanish --position below

  # With paragraph merging for fewer API calls:
  python epub_translator.py book.epub -t Korean \\
      --engine chatgpt --api-key sk-xxx --merge --merge-length 2000

  # Resume from cache:
  python epub_translator.py book.epub -t Japanese --engine chatgpt \\
      --api-key sk-xxx --cache

Supported engines: google_free, google_free_html, chatgpt, codex, claude, gemini,
                   deepl, deepl_pro, deepl_free, argos_local
        """)

    parser.add_argument('input', help='Input EPUB file path')
    parser.add_argument('-o', '--output', help='Output EPUB file path '
                        '(default: input_translated.epub)')
    parser.add_argument('-s', '--source-lang', default='English',
                        help='Source language (default: English)')
    parser.add_argument('-t', '--target-lang', required=True,
                        help='Target language (e.g., "Chinese (Simplified)", '
                        'Japanese, French)')
    parser.add_argument('--engine', default='codex',
                        choices=['google_free', 'google_free_html',
                                 'chatgpt', 'codex', 'claude', 'gemini',
                                 'deepl', 'deepl_pro', 'deepl_free',
                                 'argos_local'],
                        help='Translation engine (default: codex)')
    parser.add_argument('--api-key', help='API key for the engine')
    parser.add_argument('--model', help='Model name (for LLM engines)')
    parser.add_argument('--endpoint', help='Custom API endpoint URL')
    parser.add_argument('--prompt', help='Custom system prompt (for LLM '
                        'engines)')

    parser.add_argument('--position', default='only',
                        choices=['only', 'below', 'above', 'left', 'right'],
                        help='Translation position relative to original '
                        '(default: only = replace)')
    parser.add_argument('--original-color', help='Color for original text '
                        '(e.g., #666666)')
    parser.add_argument('--translation-color', help='Color for translated '
                        'text (e.g., #000000)')

    parser.add_argument('--merge', action='store_true',
                        help='Merge paragraphs to reduce API calls')
    parser.add_argument('--merge-length', type=int, default=1800,
                        help='Max merged content length (default: 1800)')

    parser.add_argument('--cache', action='store_true',
                        help='Enable translation cache (resume support)')
    parser.add_argument('--cache-dir', help='Cache directory path')

    parser.add_argument('--glossary', help='Glossary file path')

    parser.add_argument('--concurrency', type=int, default=None,
                        help='Concurrency limit (0 = unlimited)')
    parser.add_argument('--interval', type=float, default=None,
                        help='Request interval in seconds')
    parser.add_argument('--timeout', type=float, default=None,
                        help='Request timeout in seconds')
    parser.add_argument('--retries', type=int, default=None,
                        help='Max retry attempts per paragraph')

    parser.add_argument('--proxy', help='Proxy URL (e.g., http://host:port '
                        'or socks5://host:port)')

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output')

    return parser.parse_args()


def get_target_language_code(target_lang):
    """Best-effort BCP47-ish metadata code for the translated EPUB."""
    return GOOGLE_LANGS.get(target_lang) or DEEPL_TARGET_LANGS.get(target_lang)


def main():
    args = parse_args()

    # Validate input file
    if not os.path.isfile(args.input):
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)
    if not args.input.lower().endswith('.epub'):
        print("Error: Input file must be an EPUB file")
        sys.exit(1)

    # Set output path
    if args.output is None:
        base, ext = os.path.splitext(args.input)
        args.output = f"{base}_translated{ext}"

    print(f"Input:  {args.input}")
    print(f"Output: {args.output}")
    print(f"Engine: {args.engine}")
    print(f"Source: {args.source_lang}")
    print(f"Target: {args.target_lang}")
    print(f"Position: {args.position}")
    print()

    # --- 1. Create translation engine ---
    engine = get_engine(
        engine_name=args.engine,
        api_key=args.api_key,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        model=args.model,
        endpoint=args.endpoint,
        prompt=args.prompt,
        merge_enabled=args.merge,
        proxy=args.proxy,
        concurrency_limit=args.concurrency,
        request_interval=args.interval,
        request_timeout=args.timeout,
        request_attempt=args.retries,
    )

    # --- 2. Parse EPUB ---
    print("Parsing EPUB...")
    epub = EpubFile(args.input)
    epub.extract()

    # --- 3. Extract translatable elements ---
    print("Extracting content...")
    extraction = Extraction(epub.get_content_documents())
    elements = extraction.get_elements()
    print(f"  Found {len(elements)} translatable elements")

    if not elements:
        print("No translatable content found. Exiting.")
        sys.exit(0)

    # --- 4. Prepare element handler ---
    handler = ElementHandler(
        placeholder=engine.placeholder,
        separator=engine.separator,
        position=args.position,
        merge_enabled=args.merge,
        merge_length=args.merge_length,
        original_color=args.original_color,
        translation_color=args.translation_color,
    )
    original_group = handler.prepare_original(elements)

    # --- 5. Set up cache ---
    from lib.utils import uid
    import hashlib

    def _file_hash(path):
        """Hash file contents for cache identity."""
        h = hashlib.md5()
        try:
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
        except (OSError, TypeError):
            return ''
        return h.hexdigest()

    cache_id = uid(
        _file_hash(args.input), args.engine, args.source_lang,
        args.target_lang, str(args.merge_length if args.merge else 0),
        args.model or '', args.prompt or '', args.endpoint or '',
        _file_hash(args.glossary) if args.glossary else '')
    cache_dir = args.cache_dir or os.path.join(
        os.path.dirname(args.output), '.epub_translator_cache')
    cache = TranslationCache(cache_id, cache_dir, enabled=args.cache)
    cache.save(original_group)
    paragraphs = cache.all_paragraphs()

    cached_count = sum(1 for p in paragraphs if p.translation)
    need_translate = sum(1 for p in paragraphs if not p.translation)
    print(f"  {cached_count} cached, {need_translate} need translation")

    # --- 6. Set up glossary ---
    glossary = Glossary(engine.placeholder)
    if args.glossary:
        glossary.load_from_file(args.glossary)

    # --- 7. Translate ---
    if need_translate > 0:
        print(f"\nTranslating {need_translate} paragraphs...")
        translation = Translation(
            translator=engine,
            glossary=glossary,
            cache=cache,
            verbose=args.verbose,
        )
        translation.handle(paragraphs)
        print("\nTranslation complete!")
    else:
        print("\nAll paragraphs already cached. Skipping translation.")

    # --- 8. Apply translations to elements ---
    print("Applying translations to EPUB content...")
    paragraphs = cache.all_paragraphs()
    handler.add_translations(paragraphs)
    target_lang_code = get_target_language_code(args.target_lang)
    if target_lang_code:
        epub.update_language(target_lang_code)

    # --- 9. Write output EPUB ---
    print(f"Writing output: {args.output}")
    epub.save(args.output)
    print("Done!")

    # Cleanup
    cache.close()


if __name__ == '__main__':
    main()
