import unittest

from lib.cache import Paragraph
from lib.element_handler import ElementHandler
from lib.translator import PlaceholderValidationError, Translation


PLACEHOLDER = (
    "{{{{id_{}}}}}",
    r"({{\s*)+id\s*_\s*{}\s*(\s*}})+",
)


class DummyCache:
    def update_paragraph(self, paragraph):
        pass


class DummyEngine:
    name = "dummy"
    target_lang = "French"
    placeholder = PLACEHOLDER
    separator = "\n\n"
    merge_enabled = False
    request_attempt = 2

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def translate(self, text):
        self.calls += 1
        return self.responses.pop(0)


class DummyElement:
    def __init__(self, content):
        self.content = content
        self.ignored = False
        self.page_id = "page.xhtml"

    def get_raw(self):
        return f"<p>{self.content}</p>"

    def get_content(self):
        return self.content

    def get_attributes(self):
        return None

    def set_ignored(self, ignored):
        self.ignored = ignored


class TranslationSafetyTest(unittest.TestCase):
    def test_placeholder_validation_retries_before_accepting_translation(self):
        engine = DummyEngine([
            'Bonjour monde',
            'Bonjour {{id_r00000}} <x id="f000009">monde</x>',
        ])
        translation = Translation(
            translator=engine,
            glossary=None,
            cache=DummyCache(),
        )
        # Glossary is optional for this unit test.
        translation.glossary = type(
            "NoGlossary",
            (),
            {"replace": lambda _self, value: value,
             "restore": lambda _self, value: value},
        )()
        paragraph = Paragraph(
            1,
            "md5",
            "",
            'Hello {{id_r00000}} <x id="f000009">world</x>',
        )

        translation.translate_paragraph(paragraph)

        self.assertEqual(engine.calls, 2)
        self.assertEqual(
            paragraph.translation,
            'Bonjour {{id_r00000}} <x id="f000009">monde</x>',
        )

    def test_placeholder_validation_fails_after_configured_attempts(self):
        engine = DummyEngine(['Bonjour', 'Salut'])
        translation = Translation(
            translator=engine,
            glossary=type(
                "NoGlossary",
                (),
                {"replace": lambda _self, value: value,
                 "restore": lambda _self, value: value},
            )(),
            cache=DummyCache(),
        )
        paragraph = Paragraph(1, "md5", "", "Hello {{id_r00000}}")

        with self.assertRaises(PlaceholderValidationError):
            translation.translate_paragraph(paragraph)

    def test_merge_tokens_limits_merged_request_size(self):
        handler = ElementHandler(
            placeholder=PLACEHOLDER,
            separator="\n\n",
            position="only",
            merge_enabled=True,
            merge_length=1000,
            merge_tokens=6,
        )
        originals = handler.prepare_original([
            DummyElement("one two three four"),
            DummyElement("five six seven eight"),
        ])

        self.assertEqual(len(originals), 2)


if __name__ == "__main__":
    unittest.main()
