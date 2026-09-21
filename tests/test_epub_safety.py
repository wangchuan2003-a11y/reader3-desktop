"""Foreign EPUB fixtures check content fidelity and passive rendering boundaries."""
import base64
from pathlib import Path
import tempfile
import unittest
import warnings
from xml.etree import ElementTree
from zipfile import ZipFile

from bs4 import BeautifulSoup
from ebooklib import epub

from book_import import ImportErrorForUser, import_epub_file, validate_epub
from reader3 import clean_html_content, process_epub


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=')


def write_epub(path, content, images=()):
    book = epub.EpubBook()
    book.set_identifier('safety-fixture')
    book.set_title('完整性测试')
    book.set_language('zh')
    chapter = epub.EpubHtml(title='不会混入正文的文件标题', file_name='Text/chapter.xhtml', content=content)
    book.add_item(chapter)
    for number, (name, media_type, data) in enumerate(images):
        book.add_item(epub.EpubItem(uid='image' + str(number), file_name=name,
                                   media_type=media_type, content=data))
    book.spine = [chapter]
    book.toc = [epub.Link('Text/chapter.xhtml', '第一章', 'chapter')]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)
    return path


def rewrite_archive(source, target, replacements=None, extra=None):
    replacements, extra = replacements or {}, extra or {}
    with ZipFile(source) as before, ZipFile(target, 'w') as after:
        for member in before.infolist():
            value = before.read(member.filename)
            replacement = replacements.get(member.filename)
            if replacement:
                value = replacement(value)
            after.writestr(member, value)
        for name, value in extra.items():
            after.writestr(name, value)
    return target


class PassiveContentTest(unittest.TestCase):
    def test_invalid_and_active_urls_are_removed_without_losing_link_text(self):
        content = ('<p>正文<a href="http://[">参考</a><a href="java&#10;script:bad()">注释</a>'
                   '<a href="https://example.com/">外部参考</a></p>'
                   '<img src="images/../secret.svg"/><img src="images/valid.png" srcset="https://example.com/p.png"/>')
        soup = clean_html_content(BeautifulSoup(content, 'html.parser'))
        self.assertEqual(soup.get_text(), '正文参考注释外部参考')
        self.assertEqual([a.get('href') for a in soup.find_all('a')], [None, None, 'https://example.com/'])
        self.assertEqual([img.get('src') for img in soup.find_all('img')], [None, 'images/valid.png'])
        self.assertNotIn('srcset', str(soup))

    def test_collapsed_details_are_expanded_without_losing_chinese_paragraphs(self):
        soup = clean_html_content(BeautifulSoup(
            '<h1>第一章</h1><p>论点。</p><details><summary>解释</summary><p>不能漏掉的论证。</p></details>',
            'html.parser'))
        self.assertIsNone(soup.find('details'))
        self.assertEqual([p.get_text() for p in soup.find_all('p')], ['论点。', '解释', '不能漏掉的论证。'])

    def test_embedded_images_resolve_encoded_paths_with_url_fragments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_epub(root / 'book.epub', '<h1>第一章</h1><img src="../Images/a%20b.png?size=large#view"/>',
                                [('Images/a b.png', 'image/png', PNG)])
            parsed = process_epub(str(source), str(root / 'output'))
            img = BeautifulSoup(parsed.spine[0].content, 'html.parser').find('img')
            self.assertIsNotNone(img)
            self.assertEqual((root / 'output' / img['src']).read_bytes(), PNG)
            self.assertNotIn('不会混入正文', parsed.spine[0].text)

    def test_svg_and_remote_images_become_visible_notices_not_active_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_epub(root / 'book.epub', '<h1>第一章</h1><img src="../Images/vector.svg" alt="论证图"/>'
                                '<img src="https://example.com/track.png" alt="远程图"/>',
                                [('Images/vector.svg', 'image/svg+xml', b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>')])
            parsed = process_epub(str(source), str(root / 'output'))
            self.assertEqual(parsed.images, {})
            self.assertEqual(list((root / 'output' / 'images').iterdir()), [])
            soup = BeautifulSoup(parsed.spine[0].content, 'html.parser')
            self.assertEqual(len(soup.find_all('img')), 0)
            self.assertIn('论证图', soup.get_text())
            self.assertIn('远程图', soup.get_text())
            self.assertEqual(soup.get_text().count('图片未显示'), 2)

    def test_image_filename_cannot_publish_html_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_epub(root / 'book.epub', '<h1>第一章</h1><img src="../Images/misnamed.html"/>',
                                [('Images/misnamed.html', 'image/png', PNG)])
            parsed = process_epub(str(source), str(root / 'output'))
            self.assertTrue(all(name.endswith('.png') for name in parsed.images.values()))
            self.assertEqual(list((root / 'output').rglob('*.html')), [])

    def test_direct_converter_refuses_existing_content_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_epub(root / 'book.epub', '<h1>第一章</h1><p>正文</p>')
            output = root / 'existing'
            output.mkdir()
            (output / 'keep.txt').write_text('旧的阅读结果')
            with self.assertRaises(ValueError):
                process_epub(str(source), str(output))
            self.assertEqual((output / 'keep.txt').read_text(), '旧的阅读结果')


class ArchiveMetadataTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = write_epub(self.root / 'source.epub', '<h1>第一章</h1><p>正文</p>')

    def test_unknown_spine_item_is_rejected_instead_of_silently_skipping_text(self):
        def break_spine(value):
            package = ElementTree.fromstring(value)
            spine = package.find('{*}spine')
            extra = ElementTree.SubElement(spine, '{http://www.idpf.org/2007/opf}itemref')
            extra.set('idref', 'missing-chapter')
            return ElementTree.tostring(package)
        source = rewrite_archive(self.source, self.root / 'broken.epub', {'EPUB/content.opf': break_spine})
        with self.assertRaisesRegex(ImportErrorForUser, '缺失的正文'):
            validate_epub(source)

    def test_duplicate_resource_member_is_not_silently_selected(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            source = rewrite_archive(self.source, self.root / 'duplicate.epub',
                                     extra={'EPUB/Text/chapter.xhtml': b'<h1>different chapter</h1>'})
        with self.assertRaisesRegex(ImportErrorForUser, '重复资源路径'):
            validate_epub(source)

    def test_encrypted_content_is_identified_before_import(self):
        encrypted = b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>
            <enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
            <enc:CipherData><enc:CipherReference URI="EPUB/Text/chapter.xhtml"/></enc:CipherData>
            </enc:EncryptedData></encryption>'''
        source = rewrite_archive(self.source, self.root / 'protected.epub', extra={'META-INF/encryption.xml': encrypted})
        with self.assertRaisesRegex(ImportErrorForUser, '加密内容'):
            import_epub_file(source, self.root / 'books', 'protected.epub')
        self.assertFalse((self.root / 'books').exists())

    def test_standard_font_obfuscation_is_not_mistaken_for_content_drm(self):
        encrypted = b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>
            <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
            <enc:CipherData><enc:CipherReference URI="EPUB/Fonts/book.otf"/></enc:CipherData>
            </enc:EncryptedData></encryption>'''
        source = rewrite_archive(self.source, self.root / 'font.epub',
                                 extra={'META-INF/encryption.xml': encrypted, 'EPUB/Fonts/book.otf': b'obfuscated-font'})
        result = import_epub_file(source, self.root / 'books', 'font.epub')
        self.assertFalse(result['duplicate'])


if __name__ == '__main__':
    unittest.main()
