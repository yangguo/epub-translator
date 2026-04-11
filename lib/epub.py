"""EPUB file parser and writer.

Handles extracting EPUB contents (which is a ZIP archive),
parsing XHTML content documents via lxml, and repackaging.
"""

import os
import re
import shutil
import zipfile
import tempfile

from lxml import etree

from .utils import NS, sorted_mixed_keys


class ContentDocument:
    """Represents one XHTML content document in the EPUB."""

    def __init__(self, href, tree, file_path):
        self.href = href
        self.tree = tree
        self.file_path = file_path
        # The root element of the parsed XHTML
        self.data = tree.getroot() if tree is not None else None
        # Unique page ID
        self.id = href

    def save(self):
        """Save the modified tree back to the file."""
        self.tree.write(
            self.file_path, xml_declaration=True,
            encoding='utf-8', pretty_print=False)


class EpubFile:
    """Parse and manipulate EPUB files.

    An EPUB is a ZIP archive containing:
    - META-INF/container.xml  (points to the OPF file)
    - content.opf             (package document with manifest/spine)
    - *.xhtml / *.html        (content documents)
    - *.css, images, etc.     (resources)
    """

    def __init__(self, path):
        self.path = path
        self.temp_dir = None
        self.opf_path = None
        self.opf_dir = None
        self.content_docs = []
        self.metadata = {}

    def extract(self):
        """Extract the EPUB to a temp directory and parse contents."""
        self.temp_dir = tempfile.mkdtemp(prefix='epub_translator_')
        with zipfile.ZipFile(self.path, 'r') as zf:
            zf.extractall(self.temp_dir)

        # Find OPF file from container.xml
        container_path = os.path.join(
            self.temp_dir, 'META-INF', 'container.xml')
        container_tree = etree.parse(container_path)
        container_ns = {
            'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        rootfile = container_tree.find(
            './/c:rootfile', namespaces=container_ns)
        if rootfile is None:
            raise ValueError("Cannot find rootfile in container.xml")

        opf_relative = rootfile.get('full-path')
        self.opf_path = os.path.join(self.temp_dir, opf_relative)
        self.opf_dir = os.path.dirname(self.opf_path)

        # Parse OPF
        opf_tree = etree.parse(self.opf_path)
        opf_ns = {'opf': 'http://www.idpf.org/2007/opf',
                   'dc': 'http://purl.org/dc/elements/1.1/'}

        # Extract metadata
        self._parse_metadata(opf_tree, opf_ns)

        # Find all content documents from the manifest
        manifest = opf_tree.find('.//opf:manifest', namespaces=opf_ns)
        if manifest is None:
            raise ValueError("Cannot find manifest in OPF")

        xhtml_pattern = re.compile(
            r'\.(xhtml|html|htm|xml|xht)$', re.IGNORECASE)

        for item in manifest.findall('opf:item', namespaces=opf_ns):
            href = item.get('href', '')
            media_type = item.get('media-type', '')

            # Only process XHTML content documents
            if not (xhtml_pattern.search(href) or
                    'xhtml' in media_type or 'html' in media_type):
                continue

            file_path = os.path.join(self.opf_dir, href)
            if not os.path.isfile(file_path):
                continue

            try:
                parser = etree.XMLParser(
                    recover=True, resolve_entities=False,
                    no_network=True)
                tree = etree.parse(file_path, parser)
                doc = ContentDocument(href, tree, file_path)
                if doc.data is not None:
                    self.content_docs.append(doc)
            except Exception as e:
                print(f"  Warning: Could not parse {href}: {e}")

        # Order content documents according to the OPF <spine>
        spine = opf_tree.find('.//opf:spine', namespaces=opf_ns)
        if spine is not None:
            # Build manifest id -> href mapping
            id_to_href = {}
            for item in manifest.findall('opf:item', namespaces=opf_ns):
                id_to_href[item.get('id', '')] = item.get('href', '')
            # Build spine-ordered href list
            spine_hrefs = []
            for itemref in spine.findall('opf:itemref', namespaces=opf_ns):
                idref = itemref.get('idref', '')
                if idref in id_to_href:
                    spine_hrefs.append(id_to_href[idref])
            # Sort docs by spine position; docs not in spine go last
            spine_order = {href: i for i, href in enumerate(spine_hrefs)}
            self.content_docs.sort(
                key=lambda d: spine_order.get(d.href, len(spine_hrefs)))
        else:
            # Fallback: sort by href if no spine found
            self.content_docs.sort(
                key=lambda d: sorted_mixed_keys(d.href))

        print(f"  Parsed {len(self.content_docs)} content documents")

    def _parse_metadata(self, opf_tree, opf_ns):
        """Extract basic metadata from OPF."""
        metadata_el = opf_tree.find('.//opf:metadata', namespaces=opf_ns)
        if metadata_el is None:
            return
        for tag in ('title', 'creator', 'language', 'publisher',
                    'description', 'subject'):
            el = metadata_el.find(f'dc:{tag}', namespaces=opf_ns)
            if el is not None and el.text:
                self.metadata[tag] = el.text

    def get_content_documents(self):
        """Return the list of parsed content documents."""
        return self.content_docs

    def update_language(self, lang_code):
        """Update the language in the OPF metadata."""
        if not self.opf_path:
            return
        opf_tree = etree.parse(self.opf_path)
        opf_ns = {'opf': 'http://www.idpf.org/2007/opf',
                   'dc': 'http://purl.org/dc/elements/1.1/'}
        metadata_el = opf_tree.find('.//opf:metadata', namespaces=opf_ns)
        if metadata_el is not None:
            lang_el = metadata_el.find('dc:language', namespaces=opf_ns)
            if lang_el is not None:
                lang_el.text = lang_code
        opf_tree.write(
            self.opf_path, xml_declaration=True,
            encoding='utf-8', pretty_print=False)

    def save(self, output_path):
        """Save all modified content documents and repackage as EPUB."""
        if self.temp_dir is None:
            raise RuntimeError("EPUB not extracted yet")

        # Save all modified content documents
        for doc in self.content_docs:
            doc.save()

        # Repackage as EPUB (ZIP with specific requirements)
        # EPUB requires mimetype as the first file, uncompressed
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Write mimetype first, uncompressed
            mimetype_path = os.path.join(self.temp_dir, 'mimetype')
            if os.path.exists(mimetype_path):
                zf.write(mimetype_path, 'mimetype',
                         compress_type=zipfile.ZIP_STORED)

            # Walk the temp directory and add all other files
            for root, dirs, files in os.walk(self.temp_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, self.temp_dir)
                    # Skip mimetype (already added)
                    if arcname == 'mimetype':
                        continue
                    zf.write(full_path, arcname)

    def cleanup(self):
        """Remove temporary directory."""
        if self.temp_dir and os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def __del__(self):
        self.cleanup()
