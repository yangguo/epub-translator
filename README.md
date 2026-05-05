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
- Optionally validate the finished EPUB and write a Markdown report
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

Generate a validation report after writing the EPUB:

```bash
python epub_translator.py book.epub \
  -t "Chinese (Simplified)" \
  --validate-output \
  --validation-report book.validation.md
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
- `--validate-output`: validate the written EPUB and generate a Markdown report
- `--validation-report`: override the report path, default is the output path with `.validation.md`
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

## Validation Reports

`--validate-output` runs after the output EPUB has already been saved. It compares the source and output EPUB archives and writes a Markdown report. If validation or report writing itself fails, the CLI prints a warning and keeps the saved EPUB; validation is an optional post-write check, not part of the translation transaction.

The validator checks:

- ZIP readability and `mimetype` placement/compression
- OPF/container parseability and content document discovery
- Missing or changed non-HTML resources
- HTML/XML parseability
- Leaked translation placeholders such as `{{id_...}}`
- Lightweight structure signatures for `a`, `img`, `picture`, `source`, `h1`, `h2`, `h3`, `div`, `li`, `table`, and `figure`

Report status values:

- `PASS`: no validation findings
- `REVIEW`: warnings were found, usually structural tag-count changes that need human review
- `FAIL`: missing resources, broken archives, parse errors, or placeholder leaks were found

The validator ignores ZIP directory entries when comparing resources, because repackaging may omit explicit directory entries without losing actual files.

## How It Works

1. Extract the EPUB archive to a temporary directory.
2. Parse content documents from the OPF manifest and spine.
3. Identify translatable XHTML elements.
4. Replace reserved markup with placeholders before translation.
5. Translate paragraph-by-paragraph or in merged batches.
6. Restore preserved markup and write the translated content back into the XHTML tree.
7. Repackage the modified files as a new EPUB.
8. Optionally validate the saved EPUB and write a Markdown report.

## Testing

Run the current unit tests with:

```bash
uv run --with lxml python -m unittest discover -s tests
```

## Project Layout

```text
epub_translator.py  CLI entry point
engines/            Translation engines and language mappings
lib/                EPUB parsing, extraction, caching, translation, validation
lib/validation/     EPUB archive checks, structure signatures, Markdown reports
tests/              Unit tests
```

## Notes

- `google_free`, `google_free_html`, and `deepl_free` use unofficial endpoints and may be less stable than paid APIs.
- `codex` intentionally runs conservatively because each translation goes through the local CLI.
- `argos_local` only works for language pairs installed in the local Argos environment.
- Large books generally benefit from `--cache`, and often from `--merge` as well.
- Validation reports are advisory by default; the current CLI does not return a non-zero exit code for `FAIL` or `REVIEW` validation status.
