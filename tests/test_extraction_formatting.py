import unittest

from lxml import etree

from lib.element_handler import ElementHandler
from lib.extraction import Extraction, PageElement, get_name


XHTML_NS = "http://www.w3.org/1999/xhtml"


class DummyPage:
    def __init__(self, page_id, data):
        self.id = page_id
        self.data = data


class ExtractionFormattingTest(unittest.TestCase):
    def _parse_xml(self, markup):
        return etree.fromstring(markup.encode("utf-8"))

    def _configure_page_element(self, element):
        page_element = PageElement(element, page_id="test")
        handler = ElementHandler(
            placeholder=(
                "{{{{id_{}}}}}",
                r"({{\s*)+id\s*_\s*{}\s*(\s*}})+",
            ),
            separator="\n\n",
            position="only",
        )
        handler._configure_element(page_element)
        return page_element

    def test_inline_wrappers_survive_translation_reinsertion(self):
        body = self._parse_xml(
            f"""
            <body xmlns="{XHTML_NS}">
              <div data-testid="BylinesWrapper">
                <span>
                  <span>
                    <a href="https://example.com/author">Alex Mar</a>
                  </span>
                </span>
              </div>
            </body>
            """
        )
        element = body[0]
        page_element = self._configure_page_element(element)

        content = page_element.get_content()
        translation = content.replace("Alex Mar", "亚历克斯·马")

        page_element.add_translation(translation)

        result = etree.tostring(body, encoding="unicode")
        self.assertIn('href="https://example.com/author"', result)
        self.assertIn(">亚历克斯·马</a>", result)

    def test_lists_with_list_items_are_not_extracted_as_inline_only(self):
        document = self._parse_xml(
            f"""
            <html xmlns="{XHTML_NS}">
              <body>
                <ul class="calibre_feed_list">
                  <li>
                    <a href="https://example.com/story">
                      <h2>THE AI ISSUE</h2>
                    </a>
                  </li>
                </ul>
              </body>
            </html>
            """
        )
        extraction = Extraction([DummyPage("page.xhtml", document)])

        elements = extraction.get_elements()
        tags = [get_name(element.element) for element in elements]

        self.assertNotIn("ul", tags)
        self.assertIn("h2", tags)
