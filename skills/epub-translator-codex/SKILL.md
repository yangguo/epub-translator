---
name: epub-translator-codex
description: Use when translating EPUB files in this epub-translator project with Codex, local Codex CLI auth, cached resume support, merged chunks, or format-preservation checks.
---

# EPUB Translator Codex

## Overview

Use this project skill for long EPUB translations where format fidelity matters. Prefer the local `codex` engine, keep translation resumable, and do not call the EPUB complete until cache, archive, metadata, XML, and placeholder checks pass.

## Workflow

1. Work from the repository root.
2. Confirm `codex --version` works and the input `.epub` exists.
3. Use cache and merged chunks for magazine/book-length inputs:

```bash
python3 -u epub_translator.py INPUT.epub \
  -s English \
  -t "Chinese (Simplified)" \
  --engine codex \
  --cache \
  --merge \
  --merge-length 2500 \
  --concurrency 1 \
  --timeout 900 \
  --retries 0 \
  -o OUTPUT.zh-CN.epub
```

Use `--model MODEL` only when a model is explicitly requested. Without `--model`, the script lets the authenticated Codex CLI choose its configured/default model.

## Resume Rules

- If Codex reports a usage limit, do not switch engines unless the user explicitly changes the requirement.
- Re-run the exact same command after the reset time so the SQLite cache skips completed chunks.
- If a chunk times out, first retry the same cache identity with a longer `--timeout`; do not change `--merge-length` unless you intend to start a separate cache.
- Keep `--concurrency 1` for Codex unless the user accepts faster quota consumption.

## Verification

Run a fresh validation after the script writes the output EPUB:

```bash
sqlite3 .epub_translator_cache/CACHE_ID.db \
  'select count(*), sum(case when translation is not null and translation != "" then 1 else 0 end), count(distinct engine_name), group_concat(distinct engine_name), group_concat(distinct target_lang) from cache;'
```

Then verify the EPUB archive and XHTML:

```bash
python3 - <<'PY'
import os, re, zipfile
from lxml import etree

path = 'OUTPUT.zh-CN.epub'
assert zipfile.is_zipfile(path)
with zipfile.ZipFile(path) as zf:
    names = zf.namelist()
    assert names[0] == 'mimetype'
    assert zf.getinfo(names[0]).compress_type == zipfile.ZIP_STORED
    assert zf.testzip() is None
    container = etree.fromstring(zf.read('META-INF/container.xml'))
    ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
    opf_path = container.find('.//c:rootfile', namespaces=ns).get('full-path')
    opf = etree.fromstring(zf.read(opf_path))
    opf_ns = {'opf':'http://www.idpf.org/2007/opf','dc':'http://purl.org/dc/elements/1.1/'}
    assert opf.find('.//dc:language', namespaces=opf_ns).text == 'zh-CN'
    manifest = opf.find('.//opf:manifest', namespaces=opf_ns)
    docs = []
    for item in manifest.findall('opf:item', namespaces=opf_ns):
        href = item.get('href', '')
        media = item.get('media-type', '')
        if href.lower().endswith(('.xhtml','.html','.htm','.xml','.xht')) or 'html' in media:
            docs.append(os.path.normpath(os.path.join(os.path.dirname(opf_path), href)))
    cjk = 0
    unresolved = []
    for name in docs:
        data = zf.read(name).decode('utf-8', errors='replace')
        etree.fromstring(data.encode('utf-8'))
        cjk += len(re.findall(r'[\u4e00-\u9fff]', data))
        if '{{id_' in data or '<x id=' in data:
            unresolved.append(name)
    assert cjk > 0
    assert not unresolved, unresolved[:5]
print('verified', path)
PY
```

## Common Mistakes

- Claiming completion after the CLI says `Done!` but before EPUB validation.
- Leaving `{{id_...}}` or `<x id=...>` placeholder markers in output XHTML.
- Changing `--merge-length` during resume and wondering why the cache starts over.
- Using Google/DeepL after the user required Codex.
- Forcing a model by default instead of letting the local Codex CLI configuration decide.
