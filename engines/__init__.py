"""Translation engine implementations.

Standalone versions of the translation engines from the Calibre plugin.
No Calibre dependencies -- uses only stdlib urllib and lxml.

Supported engines:
- google_free     : Google Translate (free, no API key)
- google_free_html: Google Translate HTML (free, no API key)
- chatgpt         : OpenAI ChatGPT (API key required)
- claude          : Anthropic Claude (API key required)
- gemini          : Google Gemini (API key required)
- deepl           : DeepL API Free (API key required)
- deepl_pro       : DeepL API Pro (API key required)
- deepl_free      : DeepL Free reverse-engineered (no API key)
"""

import json
import os
import ssl
import subprocess
import tempfile
import time
import random
import urllib.request
import urllib.parse
import urllib.error
from http.client import IncompleteRead

from .languages import GOOGLE_LANGS, DEEPL_SOURCE_LANGS, DEEPL_TARGET_LANGS


class TranslationEngine:
    """Base class for all translation engines."""

    name = None
    separator = '\n\n'
    placeholder = ('{{{{id_{}}}}}', r'({{\s*)+id\s*_\s*{}\s*(\s*}})+')

    need_api_key = True
    concurrency_limit = 0
    request_interval = 0.0
    request_attempt = 3
    request_timeout = 30.0
    max_error_count = 10
    merge_enabled = False

    def __init__(self):
        self.source_lang = 'English'
        self.target_lang = ''
        self.api_key = None
        self.proxy = None
        self.proxy_handler = None

    def set_proxy(self, proxy_url):
        if proxy_url:
            self.proxy = proxy_url
            self.proxy_handler = urllib.request.ProxyHandler({
                'http': proxy_url, 'https': proxy_url})

    def _make_request(self, url, data=None, headers=None, method='POST',
                      raw=False):
        headers = headers or {}
        if isinstance(data, dict):
            if method == 'GET':
                url = url + '?' + urllib.parse.urlencode(data)
                data = None
            else:
                if 'application/json' in headers.get('Content-Type', ''):
                    data = json.dumps(data).encode('utf-8')
                else:
                    data = urllib.parse.urlencode(data).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')

        req = urllib.request.Request(
            url, data=data, headers=headers, method=method)
        ctx = ssl.create_default_context()
        try:
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
        except Exception:
            ctx = ssl._create_unverified_context()

        opener_args = [urllib.request.HTTPSHandler(context=ctx)]
        if self.proxy_handler:
            opener_args.append(self.proxy_handler)
        opener = urllib.request.build_opener(*opener_args)

        try:
            response = opener.open(req, timeout=self.request_timeout)
            if raw:
                return response
            return response.read().decode('utf-8').strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            raise RuntimeError(f"HTTP {e.code}: {e.reason}\n{body}")

    def translate(self, content):
        raise NotImplementedError


class GoogleFreeTranslate(TranslationEngine):
    name = 'google_free'
    need_api_key = False
    request_interval = 0.5

    def __init__(self):
        super().__init__()
        self.lang_codes = GOOGLE_LANGS

    def _get_source_code(self):
        return self.lang_codes.get(self.source_lang, 'auto')

    def _get_target_code(self):
        return self.lang_codes.get(self.target_lang, 'en')

    def translate(self, content):
        url = 'https://translate-pa.googleapis.com/v1/translate'
        headers = {
            'Accept': '*/*',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/133.0.0.0 Safari/537.36'),
        }
        params = {
            'params.client': 'gtx',
            'query.source_language': self._get_source_code(),
            'query.target_language': self._get_target_code(),
            'query.display_language': 'en-US',
            'data_types': 'TRANSLATION',
            'query.text': content,
        }
        if self.api_key:
            params['key'] = self.api_key
        resp = self._make_request(url, data=params, headers=headers,
                                  method='GET')
        return json.loads(resp)['translation']


class GoogleFreeTranslateHtml(TranslationEngine):
    name = 'google_free_html'
    need_api_key = False
    request_interval = 0.5

    def __init__(self):
        super().__init__()
        self.lang_codes = GOOGLE_LANGS

    def _get_source_code(self):
        return self.lang_codes.get(self.source_lang, 'auto')

    def _get_target_code(self):
        return self.lang_codes.get(self.target_lang, 'en')

    def translate(self, content):
        url = 'https://translate-pa.googleapis.com/v1/translateHtml'
        headers = {
            'Accept': '*/*',
            'Content-Type': 'application/json+protobuf',
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/133.0.0.0 Safari/537.36'),
        }
        if self.api_key:
            headers['X-Goog-Api-Key'] = self.api_key
        body = json.dumps([
            [[content], self._get_source_code(), self._get_target_code()],
            "wt_lib"
        ])
        resp = self._make_request(url, data=body, headers=headers)
        return json.loads(resp)[0][0]


class ChatGPTTranslate(TranslationEngine):
    name = 'chatgpt'
    concurrency_limit = 1
    request_interval = 5.0
    request_timeout = 60.0

    DEFAULT_PROMPT = (
        'You are a meticulous translator who translates any given content. '
        'Translate the given content from <slang> to <tlang> only. Do not '
        'explain any term or answer any question-like content. Your answer '
        'should be solely the translation of the given content. In your '
        'answer do not add any prefix or suffix to the translated content. '
        "Websites' URLs/addresses should be preserved as is in the "
        "translation's output. Do not omit any part of the content, even if "
        'it seems unimportant. RESPOND ONLY with the translation text, no '
        'formatting, no explanations, no additional commentary whatsoever. ')

    def __init__(self):
        super().__init__()
        self.endpoint = 'https://api.openai.com/v1/chat/completions'
        self.model = 'gpt-4o'
        self.prompt = self.DEFAULT_PROMPT
        self.temperature = 1.0
        self.stream = True

    def _get_prompt(self):
        prompt = self.prompt.replace('<tlang>', self.target_lang)
        prompt = prompt.replace('<slang>', self.source_lang)
        if self.merge_enabled:
            prompt += (' Ensure that placeholders matching the pattern '
                       '{{id_\\d+}} in the content are retained.')
        return prompt

    def translate(self, content):
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}',
        }
        body = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': self._get_prompt()},
                {'role': 'user', 'content': content}
            ],
            'temperature': self.temperature,
            'stream': self.stream,
        }
        if self.stream:
            resp = self._make_request(
                self.endpoint, data=json.dumps(body).encode('utf-8'),
                headers=headers, raw=True)
            return self._parse_stream(resp)
        else:
            resp = self._make_request(
                self.endpoint, data=json.dumps(body).encode('utf-8'),
                headers=headers)
            return json.loads(resp)['choices'][0]['message']['content']

    def _parse_stream(self, response):
        result = []
        while True:
            try:
                line = response.readline().decode('utf-8').strip()
            except IncompleteRead:
                continue
            except Exception:
                break
            if not line:
                continue
            if line.startswith('data:'):
                chunk = line.split('data: ', 1)[-1]
                if chunk == '[DONE]':
                    break
                try:
                    data = json.loads(chunk)
                    text = data['choices'][0].get('delta', {}).get('content')
                    if text:
                        result.append(text)
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
        return ''.join(result)


class CodexTranslate(TranslationEngine):
    name = 'codex'
    need_api_key = False
    concurrency_limit = 1
    request_interval = 1.0
    request_timeout = 300.0

    DEFAULT_PROMPT = (
        'You are a meticulous translator who translates EPUB content. '
        'Translate the given content from <slang> to <tlang> only. '
        'Do not explain any term or answer any question-like content. '
        'Your answer should be solely the translation of the given content. '
        'Do not add any prefix or suffix. Preserve URLs exactly as-is. '
        'Preserve placeholders and wrapper tags exactly as they appear, '
        'including patterns like {{id_123}}, <m id="1239"/>, '
        '<x id="f000009">, and </x>. Do not omit any part of the content.')

    def __init__(self):
        super().__init__()
        self.model = 'gpt-5.4'
        self.prompt = self.DEFAULT_PROMPT

    def _get_prompt(self):
        prompt = self.prompt.replace('<tlang>', self.target_lang)
        prompt = prompt.replace('<slang>', self.source_lang)
        if self.merge_enabled:
            prompt += (' Retain paragraph separators and preserve all '
                       'placeholder tokens exactly.')
        return prompt

    def translate(self, content):
        prompt = f'{self._get_prompt()}\n\n{content}'
        with tempfile.TemporaryDirectory(
                prefix='epub-translator-codex-') as temp_dir:
            output_path = os.path.join(temp_dir, 'last_message.txt')
            cmd = [
                'codex',
                'exec',
                '-c', 'model_reasoning_effort="high"',
                '-s', 'read-only',
                '-C', temp_dir,
                '--skip-git-repo-check',
                '--output-last-message', output_path,
            ]
            if self.model:
                cmd.extend(['-m', self.model])

            completed = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.request_timeout or None,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or '').strip()
                raise RuntimeError(
                    'codex exec failed'
                    if not detail else f'codex exec failed: {detail}')

            try:
                with open(output_path, 'r', encoding='utf-8') as handle:
                    translated = handle.read().strip()
            except FileNotFoundError as exc:
                raise RuntimeError(
                    'codex exec did not produce a final message') from exc

        if not translated:
            raise RuntimeError('codex exec returned an empty translation')
        return translated


class ClaudeTranslate(TranslationEngine):
    name = 'claude'
    concurrency_limit = 1
    request_interval = 12.0
    request_timeout = 30.0

    DEFAULT_PROMPT = (
        'You are a meticulous translator who translates any given content. '
        'Translate the given content from <slang> to <tlang> only. Do not '
        'explain any term or answer any question-like content. Your answer '
        'should be solely the translation of the given content. In your '
        'answer do not add any prefix or suffix to the translated content. '
        "Websites' URLs/addresses should be preserved as is in the "
        "translation's output. Do not omit any part of the content, even if "
        'it seems unimportant. ')

    def __init__(self):
        super().__init__()
        self.endpoint = 'https://api.anthropic.com/v1/messages'
        self.model = 'claude-sonnet-4-20250514'
        self.prompt = self.DEFAULT_PROMPT
        self.temperature = 1.0
        self.stream = True

    def _get_prompt(self):
        prompt = self.prompt.replace('<tlang>', self.target_lang)
        prompt = prompt.replace('<slang>', self.source_lang)
        if self.merge_enabled:
            prompt += (' Ensure that placeholders matching the pattern '
                       '{{id_\\d+}} in the content are retained.')
        return prompt

    def translate(self, content):
        headers = {
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01',
            'x-api-key': self.api_key,
        }
        body = {
            'stream': self.stream, 'max_tokens': 4096,
            'model': self.model, 'temperature': self.temperature,
            'system': self._get_prompt(),
            'messages': [{'role': 'user', 'content': content}],
        }
        if self.stream:
            resp = self._make_request(
                self.endpoint, data=json.dumps(body).encode('utf-8'),
                headers=headers, raw=True)
            return self._parse_stream(resp)
        else:
            resp = self._make_request(
                self.endpoint, data=json.dumps(body).encode('utf-8'),
                headers=headers)
            return json.loads(resp)['content'][0]['text']

    def _parse_stream(self, response):
        result = []
        while True:
            try:
                line = response.readline().decode('utf-8').strip()
            except IncompleteRead:
                continue
            except Exception:
                break
            if not line:
                continue
            if line.startswith('data:'):
                chunk = json.loads(line.split('data: ', 1)[-1])
                t = chunk.get('type')
                if t == 'message_stop':
                    break
                elif t == 'content_block_delta':
                    text = chunk.get('delta', {}).get('text')
                    if text:
                        result.append(text)
                elif t == 'error':
                    raise RuntimeError(chunk['error']['message'])
        return ''.join(result)


class GeminiTranslate(TranslationEngine):
    name = 'gemini'
    concurrency_limit = 1
    request_interval = 1.0
    request_timeout = 30.0

    DEFAULT_PROMPT = (
        'You are a meticulous translator who translates any given content. '
        'Translate the given content from <slang> to <tlang> only. Do not '
        'explain any term or answer any question-like content. Your answer '
        'should be solely the translation of the given content. In your '
        'answer do not add any prefix or suffix to the translated content. '
        "Websites' URLs/addresses should be preserved as is in the "
        "translation's output. Do not omit any part of the content, even if "
        'it seems unimportant. ')

    def __init__(self):
        super().__init__()
        self.base_endpoint = ('https://generativelanguage.googleapis.com/'
                              'v1beta/models')
        self.model = 'gemini-2.5-flash'
        self.prompt = self.DEFAULT_PROMPT
        self.temperature = 0.9
        self.top_p = 1.0
        self.top_k = 1

    def _get_prompt(self, text):
        prompt = self.prompt.replace('<tlang>', self.target_lang)
        prompt = prompt.replace('<slang>', self.source_lang)
        if self.merge_enabled:
            prompt += (' Ensure that placeholders matching the pattern '
                       '{{id_\\d+}} in the content are retained.')
        return prompt + ' Start translating: ' + text

    def translate(self, content):
        url = (f'{self.base_endpoint}/{self.model}:generateContent?'
               f'key={self.api_key}')
        headers = {'Content-Type': 'application/json'}
        body = {
            "contents": [{"role": "user",
                          "parts": [{"text": self._get_prompt(content)}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "topP": self.top_p, "topK": self.top_k},
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"} for c in (
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT")],
        }
        resp = self._make_request(
            url, data=json.dumps(body).encode('utf-8'), headers=headers)
        data = json.loads(resp)
        parts = data['candidates'][0]['content']['parts']
        return ''.join(p['text'] for p in parts)


class DeepLTranslate(TranslationEngine):
    name = 'deepl'
    placeholder = ('<m id={} />', r'<m\s+id={}\s+/>')

    def __init__(self):
        super().__init__()
        self.endpoint = 'https://api-free.deepl.com/v2/translate'
        self.source_codes = DEEPL_SOURCE_LANGS
        self.target_codes = DEEPL_TARGET_LANGS

    def translate(self, content):
        headers = {'Authorization': f'DeepL-Auth-Key {self.api_key}'}
        body = {
            'text': content,
            'target_lang': self.target_codes.get(self.target_lang, 'EN'),
        }
        src = self.source_codes.get(self.source_lang)
        if src:
            body['source_lang'] = src
        resp = self._make_request(self.endpoint, data=body, headers=headers)
        return json.loads(resp)['translations'][0]['text']


class DeepLProTranslate(DeepLTranslate):
    name = 'deepl_pro'

    def __init__(self):
        super().__init__()
        self.endpoint = 'https://api.deepl.com/v2/translate'


class DeepLFreeTranslate(TranslationEngine):
    name = 'deepl_free'
    need_api_key = False
    placeholder = ('<m id={} />', r'<m\s+id={}\s+/>')
    concurrency_limit = 1
    request_interval = 1.0

    def __init__(self):
        super().__init__()
        self.source_codes = DEEPL_SOURCE_LANGS
        self.target_codes = DEEPL_TARGET_LANGS

    def translate(self, content):
        url = 'https://www2.deepl.com/jsonrpc?client=chrome-extension,1.5.1'
        headers = {
            'Accept': '*/*',
            'Authorization': 'None',
            'Content-Type': 'application/json; charset=utf-8',
            'User-Agent': ('DeepLBrowserExtension/1.5.1 Mozilla/5.0 '
                           '(Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36'),
            'Origin': 'chrome-extension://cofdbpoegempjloogbagkncekinflcnj',
            'Referer': 'https://www.deepl.com/',
        }
        uid = random.randint(1000000000, 9999999999)
        count_i = content.count('i')
        ts = int(time.time() * 1000)
        if count_i > 0:
            count_i += 1
            ts = ts - ts % count_i + count_i
        target_lang = self.target_codes.get(self.target_lang, 'EN')
        regional_variant = {}
        if '-' in target_lang:
            portions = target_lang.split('-')
            regional_variant['regionalVariant'] = '-'.join(
                [portions[0].lower(), portions[1]])
            target_lang = portions[0]
        body = json.dumps({
            'jsonrpc': '2.0', 'method': 'LMT_handle_texts',
            'params': {
                'commonJobParams': regional_variant,
                'texts': [{'text': content}], 'splitting': 'newlines',
                'lang': {
                    'source_lang_user_selected':
                        self.source_codes.get(self.source_lang, 'auto'),
                    'target_lang': target_lang},
                'timestamp': ts},
            'id': uid
        }, separators=(',', ':'))
        if (uid + 3) % 13 == 0 or (uid + 5) % 29 == 0:
            body = body.replace('"method":"', '"method" : "')
        else:
            body = body.replace('"method":"', '"method": "')
        resp = self._make_request(url, data=body.encode('utf-8'),
                                  headers=headers)
        return json.loads(resp)['result']['texts'][0]['text']


class ArgosLocalTranslate(TranslationEngine):
    """Offline translation via installed Argos Translate packages."""

    name = 'argos_local'
    need_api_key = False
    concurrency_limit = 1
    request_timeout = 0.0
    # Suffix avoids the all-zero placeholder that Argos tends to mangle.
    placeholder = (
        '<m id="{}9"/>',
        r'<m\s+id\s*=\s*["\']?{}9["\']?\s*/\s*>'
    )

    _LANG_ALIASES = {
        'english': 'en',
        'chinese': 'zh',
        'chinese (simplified)': 'zh',
        'simplified chinese': 'zh',
    }

    def __init__(self):
        super().__init__()
        self._translation = None

    def _lang_code(self, language_name):
        key = language_name.strip().lower()
        alias = self._LANG_ALIASES.get(key)
        if alias:
            return alias
        # Query argostranslate's installed languages for a name match
        try:
            import argostranslate.translate as argos_translate
            for lang in argos_translate.get_installed_languages():
                if lang.name.lower() == key:
                    return lang.code
        except Exception:
            pass
        return key

    def _load_translation(self):
        if self._translation is not None:
            return self._translation

        try:
            import argostranslate.translate as argos_translate
        except ImportError as exc:
            raise RuntimeError(
                "Engine 'argos_local' requires argostranslate. "
                "Install it first, or run via "
                "`uv run --with argostranslate python epub_translator.py ...`."
            ) from exc

        installed = argos_translate.get_installed_languages()
        source_code = self._lang_code(self.source_lang)
        target_code = self._lang_code(self.target_lang)

        from_lang = next((lang for lang in installed if lang.code == source_code),
                         None)
        to_lang = next((lang for lang in installed if lang.code == target_code),
                       None)
        if from_lang is None or to_lang is None:
            installed_codes = ', '.join(sorted(lang.code for lang in installed))
            raise RuntimeError(
                "Engine 'argos_local' needs installed Argos language packs for "
                f"{source_code}->{target_code}. Installed languages: "
                f"{installed_codes or 'none'}"
            )

        self._translation = from_lang.get_translation(to_lang)
        return self._translation

    def translate(self, content):
        return self._load_translation().translate(content)


# --- Engine Registry ---
ENGINE_MAP = {
    'google_free': GoogleFreeTranslate,
    'google_free_html': GoogleFreeTranslateHtml,
    'chatgpt': ChatGPTTranslate,
    'codex': CodexTranslate,
    'claude': ClaudeTranslate,
    'gemini': GeminiTranslate,
    'deepl': DeepLTranslate,
    'deepl_pro': DeepLProTranslate,
    'deepl_free': DeepLFreeTranslate,
    'argos_local': ArgosLocalTranslate,
}


def get_engine(engine_name, api_key=None, source_lang='English',
               target_lang='', model=None, endpoint=None, prompt=None,
               merge_enabled=False, proxy=None, concurrency_limit=None,
               request_interval=None, request_timeout=None,
               request_attempt=None):
    """Create and configure a translation engine."""
    cls = ENGINE_MAP.get(engine_name)
    if cls is None:
        raise ValueError(f"Unknown engine: {engine_name}. "
                         f"Available: {', '.join(ENGINE_MAP.keys())}")
    engine = cls()
    engine.source_lang = source_lang
    engine.target_lang = target_lang
    engine.merge_enabled = merge_enabled
    if api_key:
        engine.api_key = api_key
    elif engine.need_api_key:
        raise ValueError(f"Engine '{engine_name}' requires --api-key")
    if model and hasattr(engine, 'model'):
        engine.model = model
    if endpoint and hasattr(engine, 'endpoint'):
        engine.endpoint = endpoint
    if prompt and hasattr(engine, 'prompt'):
        engine.prompt = prompt
    if proxy:
        engine.set_proxy(proxy)
    if concurrency_limit is not None and concurrency_limit >= 0:
        engine.concurrency_limit = concurrency_limit
    if request_interval is not None and request_interval >= 0:
        engine.request_interval = request_interval
    if request_timeout is not None and request_timeout > 0:
        engine.request_timeout = request_timeout
    if request_attempt is not None and request_attempt >= 0:
        engine.request_attempt = request_attempt
    return engine
