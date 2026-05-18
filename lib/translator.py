"""Translation orchestration: glossary, retry, progress, async concurrency."""

import re
import sys
import time
import asyncio
from types import GeneratorType


class Glossary:
    """Glossary support: replace terms with placeholders before translation,
    restore target-language terms after translation."""

    def __init__(self, placeholder):
        self.placeholder = placeholder
        self.glossary = []

    def load_from_file(self, path):
        """Load glossary from file.

        Format: pairs of lines separated by blank lines.
        First line = source term, second line = target term.
        If only one line, the term is preserved as-is.
        """
        try:
            with open(path, 'r', newline=None) as f:
                content = f.read().strip()
        except Exception:
            return
        if not content:
            return
        groups = re.split(r'\n{2,}', content.strip('\ufeff'))
        for group in filter(str.strip, groups):
            lines = group.split('\n')
            self.glossary.append(
                (lines[0], lines[0] if len(lines) < 2 else lines[1]))

    def replace(self, content):
        """Replace glossary source terms with placeholders."""
        for wid, words in enumerate(self.glossary):
            replacement = self.placeholder[0].format(format(wid, '06'))
            content = content.replace(words[0], replacement)
        return content

    def restore(self, content):
        """Restore glossary target terms from placeholders."""
        for wid, words in enumerate(self.glossary):
            pattern = self.placeholder[1].format(format(wid, '06'))
            content = re.sub(pattern, lambda _: words[1], content)
        return content


class PlaceholderValidationError(RuntimeError):
    """Raised when an engine response drops structure-preservation markers."""


def _marker_pattern(marker):
    """Build a permissive regex for placeholders with zero-padded ids."""
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


def _rendered_template_parts(template):
    """Return literal prefix/suffix around the template's marker field."""
    sentinel = '__EPUB_TRANSLATOR_MARKER__'
    rendered = template.format(sentinel)
    if sentinel not in rendered:
        return None
    return rendered.split(sentinel, 1)


def _extract_engine_markers(text, placeholder):
    """Extract marker ids rendered with an engine placeholder template."""
    parts = _rendered_template_parts(placeholder[0])
    if not parts:
        return []
    prefix, suffix = parts
    pattern = re.compile(re.escape(prefix) + r'(.+?)' + re.escape(suffix))
    return pattern.findall(text or '')


def _validate_engine_placeholders(original, translation, placeholder):
    """Return validation errors for missing reserved placeholders/wrappers."""
    errors = []
    for marker in _extract_engine_markers(original, placeholder):
        pattern = placeholder[1].format(_marker_pattern(marker))
        if re.search(pattern, translation or '') is None:
            errors.append(f"missing placeholder {placeholder[0].format(marker)}")

    wrapper_ids = re.findall(
        r'<\s*x\s+id\s*=\s*["\']?([^"\'>\s]+)["\']?\s*>',
        original or '',
        flags=re.IGNORECASE,
    )
    for wrapper_id in wrapper_ids:
        opener = (
            r'<\s*x\s+id\s*=\s*["\']?' +
            re.escape(wrapper_id) +
            r'["\']?\s*>'
        )
        if re.search(opener, translation or '', flags=re.IGNORECASE) is None:
            errors.append(f"missing wrapper <x id=\"{wrapper_id}\">")

    expected_closers = len(re.findall(
        r'</\s*x\s*>', original or '', flags=re.IGNORECASE))
    actual_closers = len(re.findall(
        r'</\s*x\s*>', translation or '', flags=re.IGNORECASE))
    if actual_closers < expected_closers:
        errors.append(
            f"missing wrapper closing tag </x> "
            f"({actual_closers}/{expected_closers})")

    return errors


class Translation:
    """Manages the translation of all paragraphs with retries,
    concurrency, caching, and progress reporting."""

    def __init__(self, translator, glossary, cache, verbose=False):
        self.translator = translator
        self.glossary = glossary
        self.cache = cache
        self.verbose = verbose
        self.total = 0
        self.translated_count = 0

    def translate_text(self, text, retry=0, interval=0):
        """Translate a single text with retry logic."""
        try:
            result = self.translator.translate(text)
            return result
        except Exception as e:
            if retry >= self.translator.request_attempt:
                raise RuntimeError(
                    f"Translation failed after {retry} retries: {e}")
            retry += 1
            interval += 5
            if self.verbose:
                logged = text[:200] + '...' if len(text) > 200 else text
                print(f"  Retry {retry}/{self.translator.request_attempt} "
                      f"(sleeping {interval}s): {logged[:80]}...")
                print(f"  Error: {e}")
            time.sleep(interval)
            return self.translate_text(text, retry, interval)

    def translate_paragraph(self, paragraph):
        """Translate a single paragraph (skip if cached)."""
        if paragraph.translation:
            paragraph.is_cache = True
            return

        max_attempts = max(1, self.translator.request_attempt or 1)
        validation_errors = []

        for attempt in range(max_attempts):
            text = self.glossary.replace(paragraph.original)
            result = self.translate_text(text)

            # Handle streaming generators
            if isinstance(result, GeneratorType):
                result = ''.join(result)

            result = self.glossary.restore(result).strip()
            validation_errors = _validate_engine_placeholders(
                paragraph.original, result, self.translator.placeholder)
            if not validation_errors:
                paragraph.translation = result
                break

            if self.verbose:
                print(
                    f"  Placeholder validation failed "
                    f"({attempt + 1}/{max_attempts}): "
                    f"{'; '.join(validation_errors[:3])}")
            if attempt + 1 < max_attempts:
                interval = max(
                    0, getattr(self.translator, 'request_interval', 0) or 0)
                if interval:
                    time.sleep(interval)
        else:
            raise PlaceholderValidationError(
                "Translation did not preserve required placeholders: " +
                "; ".join(validation_errors))

        # Alignment check for merged paragraphs
        if self.translator.merge_enabled:
            paragraph.do_alignment(self.translator.separator)

        paragraph.engine_name = self.translator.name
        paragraph.target_lang = self.translator.target_lang
        paragraph.is_cache = False

    def _process_done(self, paragraph):
        """Called after a paragraph is translated."""
        self.translated_count += 1
        status = 'cached' if paragraph.is_cache else 'translated'
        # Save to cache
        if not paragraph.is_cache and paragraph.translation:
            self.cache.update_paragraph(paragraph)
        # Progress indicator
        pct = self.translated_count / self.total * 100
        original_preview = paragraph.original[:60].replace('\n', ' ')
        print(f"  [{self.translated_count}/{self.total}] "
              f"({pct:.0f}%) {status}: {original_preview}...")
        if self.verbose and paragraph.translation and not paragraph.is_cache:
            trans_preview = paragraph.translation[:80].replace('\n', ' ')
            print(f"    -> {trans_preview}")

    def handle(self, paragraphs):
        """Translate all paragraphs using async concurrency."""
        self.total = len(paragraphs)
        if self.total < 1:
            print("  No content to translate.")
            return

        char_count = sum(len(p.original) for p in paragraphs)
        print(f"  Items: {self.total}, Characters: {char_count}")

        start_time = time.time()

        # Use asyncio handler for concurrency
        handler = AsyncHandler(
            paragraphs=paragraphs,
            concurrency_limit=(
                self.translator.concurrency_limit or len(paragraphs)),
            translate_fn=self.translate_paragraph,
            process_fn=self._process_done,
            request_interval=self.translator.request_interval,
        )
        handler.run()

        elapsed = round((time.time() - start_time) / 60, 2)
        print(f"  Time: {elapsed} minutes")


class AsyncHandler:
    """Async worker pool for concurrent translation.

    Mirrors the plugin's Handler class: N translation workers pulling
    from a queue, plus one processing worker for callbacks.
    """

    def __init__(self, paragraphs, concurrency_limit, translate_fn,
                 process_fn, request_interval):
        self.queue = asyncio.Queue()
        self.done_queue = asyncio.Queue()
        self._failure = None

        for p in paragraphs:
            self.queue.put_nowait(p)

        self.concurrency_limit = min(
            concurrency_limit, self.queue.qsize()) or self.queue.qsize()
        self.translate_fn = translate_fn
        self.process_fn = process_fn
        self.request_interval = request_interval

    async def _translation_worker(self):
        while True:
            if self._failure is not None:
                return
            paragraph = await self.queue.get()
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self.translate_fn, paragraph)
                paragraph.error = None
                if self.queue.qsize() > 0 and not paragraph.is_cache:
                    await asyncio.sleep(self.request_interval)
                self.done_queue.put_nowait(paragraph)
                self.queue.task_done()
            except Exception as e:
                paragraph.error = str(e)
                self._failure = e
                self.queue.task_done()
                await self._drain_queue()
                return

    async def _processing_worker(self):
        while True:
            paragraph = await self.done_queue.get()
            self.process_fn(paragraph)
            self.done_queue.task_done()

    async def _drain_queue(self):
        """Drain remaining items from the work queue."""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def _run(self):
        tasks = []
        for _ in range(self.concurrency_limit):
            tasks.append(asyncio.create_task(self._translation_worker()))
        tasks.append(asyncio.create_task(self._processing_worker()))

        await self.queue.join()
        await self.done_queue.join()

        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

        if self._failure is not None:
            raise RuntimeError(
                f"Translation aborted: {self._failure}") from self._failure

    def run(self):
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy())
        try:
            loop = asyncio.get_event_loop()
        except Exception:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run())
