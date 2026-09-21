import unittest
import tempfile
from pathlib import Path
from bs4 import BeautifulSoup
from ebooklib import epub
from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, process_epub, clean_html_content
from chapters import ChapterIndex, normalized, reference
from server import prepare_content, reading_url


def toc(title, href, children=None):
    file, _, anchor = href.partition('#')
    return TOCEntry(title, href, file, anchor, children or [])


def book(htmls, entries):
    return Book(BookMetadata('Test', 'zh-CN'),
                [ChapterContent(str(i), f'{i}.xhtml', str(i), html, '', i) for i, html in enumerate(htmls)],
                entries, {}, 'test.epub', '')


def text(index, chapter):
    return ''.join(BeautifulSoup(html, 'html.parser').get_text() for _, html in index.fragments(chapter))


class ChapterBoundaryTest(unittest.TestCase):
    def test_one_chapter_across_files_and_nested_end_boundary(self):
        b = book(['<h1>第一章</h1><p>开头</p>', '<p>跨文件续文</p><div><p>结尾</p><h1 id="b">第二章</h1><p>不应混入</p></div>'],
                 [toc('第一章','0.xhtml'), toc('第二章','1.xhtml#b')])
        x = ChapterIndex(b)
        self.assertTrue(x.chapters[0].verified)
        self.assertEqual(text(x,x.chapters[0]), '第一章开头跨文件续文结尾')
        self.assertEqual(text(x,x.chapters[1]), '第二章不应混入')

    def test_multiple_chapters_in_single_wrapper_no_loss_or_duplicates(self):
        html = '<div><h1 id="a">第一章</h1><p>A<strong>B</strong>C</p><h1 id="b">第二章</h1><p>D</p></div>'
        x = ChapterIndex(book([html],[toc('第一章','0.xhtml#a'),toc('第二章','0.xhtml#b')]))
        self.assertEqual([text(x,c) for c in x.chapters], ['第一章ABC','第二章D'])
        self.assertEqual(''.join(text(x,c) for c in x.chapters),BeautifulSoup(html,'html.parser').get_text())

    def test_erroneous_footnote_anchor_corrected_to_unique_title(self):
        x = ChapterIndex(book(['<h1>第十六章　选择一个议题</h1><p>开头<a id="note">注</a>后文</p>'],
                              [toc('第十六章　选择一个议题','0.xhtml#note')]))
        self.assertTrue(x.chapters[0].note)
        self.assertTrue(text(x,x.chapters[0]).startswith('第十六章'))
        self.assertIn('开头',text(x,x.chapters[0]))

    def test_parts_and_appendix_are_not_included_in_chapters(self):
        b = book(['<h1>第一章</h1><p>A</p>','<h1>第二篇</h1><p>篇前言</p>','<h1>第二章</h1><p>B</p>','<h1>索引</h1>'],
                 [toc('第一篇','0.xhtml',[toc('第一章','0.xhtml')]),
                  toc('第二篇','1.xhtml',[toc('第二章','2.xhtml')]),toc('索引','3.xhtml')])
        x = ChapterIndex(b)
        self.assertEqual([text(x,c) for c in x.chapters],['第一章A','第二章B'])

    def test_unresolved_next_boundary_disables_full_copy(self):
        x=ChapterIndex(book(['<h1>第一章</h1><p>A</p>'],[toc('第一章','0.xhtml'),toc('第二章','missing.xhtml')]))
        self.assertFalse(x.chapters[0].verified)
        self.assertEqual(x.fragments(x.chapters[0]),[])

    def test_unlisted_peer_heading_disables_copy(self):
        x=ChapterIndex(book(['<h1>第一章</h1><p>A</p><h1>附录</h1><p>B</p>'],[toc('第一章','0.xhtml')]))
        self.assertFalse(x.chapters[0].verified)

    def test_misnested_numbered_chapter_does_not_get_copied_twice(self):
        x = ChapterIndex(book(
            ['<h1 id="a">第一章</h1><p>A</p><h2 id="b">第二章</h2><p>B</p>'],
            [toc('第一章', '0.xhtml#a', [toc('第二章', '0.xhtml#b')])]))
        self.assertFalse(x.chapters[0].verified)
        self.assertEqual(x.fragments(x.chapters[0]), [])
        self.assertTrue(x.chapters[1].verified)
        self.assertEqual(text(x, x.chapters[1]), '第二章B')

    def test_unlisted_numbered_chapter_at_lower_heading_level_is_not_swallowed(self):
        x = ChapterIndex(book(
            ['<h1>第一章</h1><p>A</p><h2>第二章</h2><p>B</p>'],
            [toc('第一章', '0.xhtml')]))
        self.assertFalse(x.chapters[0].verified)

    def test_invalid_links_are_unresolved_without_crashing_chapter_index(self):
        for href in ('http://[', '../../outside.xhtml', '/outside.xhtml', r'\\remote\book.xhtml'):
            with self.subTest(href=href):
                self.assertIsNone(reference(href, 'Text/one.xhtml'))
        x = ChapterIndex(book(['<h1>第一章</h1><p>A</p>'],
                              [toc('第一章', '0.xhtml'), toc('第二章', 'http://[')]))
        self.assertFalse(x.chapters[0].verified)

    def test_missing_child_anchor_disables_copy(self):
        x=ChapterIndex(book(['<h1>第一章</h1>'],[toc('第一章','0.xhtml',[toc('小节','0.xhtml#missing')])]))
        self.assertFalse(x.chapters[0].verified)

    def test_duplicate_matching_titles_are_not_guessed(self):
        x=ChapterIndex(book(['<h1>第一章</h1><p>A</p><h1>第一章</h1>'],[toc('第一章','0.xhtml')]))
        self.assertFalse(x.chapters[0].verified)

    def test_footnotes_links_and_ids_survive_merged_documents(self):
        b=book(['<h1>第一章</h1><p id="ref"><a href="1.xhtml#note">1</a></p>',
                '<p id="note">注释<a href="0.xhtml#ref">返回</a></p><h1 id="end">第二章</h1>'],
               [toc('第一章','0.xhtml'),toc('第二章','1.xhtml#end')])
        x=ChapterIndex(b)
        html=prepare_content('test_data',x,x.fragments(x.chapters[0]))
        self.assertIn('id="r1-note"',html)
        self.assertIn('href="/chapter/test_data/0#r1-note"',html)
        self.assertIn('href="/chapter/test_data/0#r0-ref"',html)

    def test_long_chapter_does_not_truncate(self):
        paragraphs=''.join(f'<p>段落{i}：判断需要证据。</p>' for i in range(12000))
        x=ChapterIndex(book(['<h1>第一章</h1>'+paragraphs],[toc('第一章','0.xhtml')]))
        soup=BeautifulSoup(x.fragments(x.chapters[0])[0][1],'html.parser')
        self.assertEqual(len(soup.select('p')),12000)
        self.assertTrue(soup.get_text().endswith('段落11999：判断需要证据。'))


class ImportIntegrityTest(unittest.TestCase):
    def test_active_markup_and_remote_resources_removed(self):
        s=clean_html_content(BeautifulSoup('<h1 onclick="x()" style="color:red">标题</h1><script>bad()</script><img src="https://example.com/t.png" onerror="x()"><a href="javascript:x()">正文</a><iframe src="https://example.com"></iframe>', 'html.parser'))
        self.assertEqual(s.get_text(),'标题正文')
        self.assertNotIn('onclick',str(s))
        self.assertNotIn('onerror',str(s))
        self.assertNotIn('javascript',str(s))
        self.assertNotIn('https://',str(s))

    def test_same_named_images_resolve_by_full_path(self):
        b=epub.EpubBook(); b.set_identifier('images'); b.set_title('Images'); b.set_language('zh')
        c=epub.EpubHtml(title='第一章',file_name='text/one.xhtml',content='<h1>第一章</h1><img src="../a/pic.png"/><img src="../b/pic.png"/>')
        b.add_item(c)
        b.add_item(epub.EpubItem(uid='a',file_name='a/pic.png',media_type='image/png',content=b'first'))
        b.add_item(epub.EpubItem(uid='b',file_name='b/pic.png',media_type='image/png',content=b'second'))
        b.spine=[c]; b.toc=[epub.Link('text/one.xhtml','第一章','c')]; b.add_item(epub.EpubNcx());b.add_item(epub.EpubNav())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder); epub.write_epub(str(path/'test.epub'),b)
            parsed=process_epub(str(path/'test.epub'),str(path/'output'))
            imgs=BeautifulSoup(parsed.spine[0].content,'html.parser').select('img')
            self.assertNotEqual(imgs[0]['src'],imgs[1]['src'])
            self.assertEqual([(path/'output'/img['src']).read_bytes() for img in imgs],[b'first',b'second'])


if __name__ == '__main__':
    unittest.main()
