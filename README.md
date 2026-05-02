# EPUB Translator

Standalone command-line EPUB translation with structure-preserving output.

This project extracts XHTML content from an EPUB, translates the text with a selected engine, and writes a new EPUB while keeping links, inline markup, and overall document structure intact. It is based on the workflow from the Ebook-Translator Calibre plugin, but runs as a plain Python CLI.

## Highlights

- Translate EPUBs without Calibre
- Preserve XHTML structure, inline formatting, and reserved markup
- Output translated-only or bilingual editions
- Merge paragraphs to reduce API calls for LLM engines
- Resume interrupted runs with a SQLite cache
- Apply glossary substitutions before translation
- Support online APIs, the local Codex CLI, and offline Argos Translate

## Supported Engines

| Engine | Description | API key |
| --- | --- | --- |
| `google_free` | Google Translate free endpoint | No |
| `google_free_html` | Google Translate HTML endpoint | No |
| `chatgpt` | OpenAI Chat Completions API | Yes |
| `codex` | Local `codex exec` workflow | No |
| `claude` | Anthropic Messages API | Yes |
| `gemini` | Google Gemini API | Yes |
| `deepl` | DeepL API Free endpoint | Yes |
| `deepl_pro` | DeepL Pro API endpoint | Yes |
| `deepl_free` | Unofficial DeepL web flow | No |
| `argos_local` | Offline Argos Translate packages | No |

## Requirements

- Python 3.9+
- `lxml`
- `codex` installed and authenticated for the default engine
- Optional, depending on engine:
  - `argostranslate` with installed language packs for `--engine argos_local`
  - Provider API keys for OpenAI, Anthropic, Gemini, and DeepL API engines

## Installation

The project runs directly from the repository.

```bash
pip install lxml
```

Or use `uv`:

```bash
uv run --with lxml python epub_translator.py --help
```

For Argos local translation:

```bash
uv run --with argostranslate python epub_translator.py --help
```

## Quick Start

Translate with the default local Codex CLI engine:

```bash
python epub_translator.py book.epub -t "Chinese (Simplified)"
```

Create a bilingual edition with the translation below the original:

```bash
python epub_translator.py book.epub -t Spanish --position below
```

Use OpenAI:

```bash
python epub_translator.py book.epub \
  -t Japanese \
  --engine chatgpt \
  --api-key "$OPENAI_API_KEY" \
  --model gpt-4o
```

Use the local Codex CLI:

```bash
python epub_translator.py book.epub \
  -t "Chinese (Simplified)" \
  --engine codex
```

By default, the `codex` engine uses the locally authenticated Codex CLI's
configured model. Pass `--model` only when you want to force a specific model.

Use offline Argos:

```bash
uv run --with argostranslate python epub_translator.py book.epub \
  -s English \
  -t "Chinese (Simplified)" \
  --engine argos_local
```

## Common Options

- `-t, --target-lang`: required target language
- `-s, --source-lang`: source language, default `English`
- `--engine`: translation backend, default `codex`
- `--api-key`: provider API key when required
- `--model`: model override for LLM engines
- `--endpoint`: custom endpoint for compatible APIs
- `--prompt`: custom system prompt for LLM engines
- `--cache`: enable SQLite-backed resume support
- `--cache-dir`: override cache directory
- `--merge`: merge paragraphs before translation
- `--merge-length`: max merged payload length
- `--glossary`: glossary file path
- `--concurrency`, `--interval`, `--timeout`, `--retries`: engine overrides
- `--proxy`: HTTP or SOCKS proxy URL
- `-o, --output`: output EPUB path
- `-v, --verbose`: verbose progress and retry output

## Output Modes

`--position` controls how translations are inserted:

- `only`: replace the original text
- `below`: add translation after the original
- `above`: add translation before the original
- `left`: bilingual two-column layout with translation on the left
- `right`: bilingual two-column layout with translation on the right

`--original-color` and `--translation-color` can be used to style bilingual output.

## Cache and Glossary

With `--cache`, the tool stores progress in `.epub_translator_cache/` next to the output file unless `--cache-dir` is set. This is useful for long LLM-backed runs and for resuming interrupted jobs.

Glossary files are grouped by blank lines:

- Two lines: source term, then target term
- One line: preserve the term as-is

Example:

```text
OpenAI

Codex
Codex

artificial intelligence
intelligence artificielle
```

## How It Works

1. Extract the EPUB archive to a temporary directory.
2. Parse content documents from the OPF manifest and spine.
3. Identify translatable XHTML elements.
4. Replace reserved markup with placeholders before translation.
5. Translate paragraph-by-paragraph or in merged batches.
6. Restore preserved markup and write the translated content back into the XHTML tree.
7. Repackage the modified files as a new EPUB.

## Testing

Run the current unit tests with:

```bash
uv run --with lxml python -m unittest discover -s tests
```

## Project Layout

```text
epub_translator.py  CLI entry point
engines/            Translation engines and language mappings
lib/                EPUB parsing, extraction, caching, and translation flow
tests/              Unit tests
```

## Notes

- `google_free`, `google_free_html`, and `deepl_free` use unofficial endpoints and may be less stable than paid APIs.
- `codex` intentionally runs conservatively because each translation goes through the local CLI.
- `argos_local` only works for language pairs installed in the local Argos environment.
- Large books generally benefit from `--cache`, and often from `--merge` as well.
