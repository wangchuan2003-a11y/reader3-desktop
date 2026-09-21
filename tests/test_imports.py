"""Regression checks for import integrity; fixtures are generated, not user books."""

import asyncio
import hashlib
import json
import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from ebooklib import epub
from starlette.requests import Request

import book_import
import server
from reader3 import process_epub, save_to_pickle


def make_epub(path, content='<h1>第一章</h1><p>判断需要证据。</p>', title='导入测试'):
    book = epub.EpubBook()
    book.set_identifier('reader3-import-fixture')
    book.set_title(title)
    book.set_language('zh-CN')
    book.add_author('测试作者')
    chapter = epub.EpubHtml(title='第一章', file_name='chapter.xhtml', content=content)
    book.add_item(chapter)
    book.spine = [chapter]
    book.toc = [epub.Link('chapter.xhtml', '第一章', 'chapter')]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)
    return path


def snapshot(folder):
    """Detect changes both to user-visible books and to leftover staging files."""
    return {str(path.relative_to(folder)): path.read_bytes()
            for path in folder.rglob('*') if path.is_file()}


class EpubImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.books = self.root / 'books'
        self.books.mkdir()
        self.source = make_epub(self.root / 'upload.epub')

    def import_book(self, path=None, filename='测试书.epub'):
        return book_import.import_epub_file(path or self.source, self.books, filename)

    def add_legacy_book(self, source_file='legacy.epub'):
        folder = self.books / 'legacy_data'
        parsed = process_epub(str(self.source), str(folder))
        parsed.source_file = source_file
        save_to_pickle(parsed, str(folder))
        return folder

    def test_valid_import_preserves_source_and_commits_readable_book(self):
        original = self.source.read_bytes()
        result = self.import_book()
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['title'], '导入测试')
        self.assertTrue(result['book_id'].endswith('_data'))
        self.assertEqual(Path(result['book_id']).name, result['book_id'])
        self.assertEqual(result['reading_url'], '/chapter/' + result['book_id'] + '/0')
        destination = self.books / result['book_id']
        self.assertEqual((destination / 'source.epub').read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)
        metadata = json.loads((destination / 'import.json').read_text())
        self.assertEqual(metadata['sha256'], hashlib.sha256(original).hexdigest())
        with (destination / 'book.pkl').open('rb') as handle:
            parsed = pickle.load(handle)
        self.assertEqual(parsed.metadata.title, '导入测试')
        self.assertIn('判断需要证据', parsed.spine[0].content)
        self.assertEqual([p.name for p in self.books.iterdir()], [result['book_id']])

    def test_same_bytes_under_new_filename_reuses_book_without_rewriting(self):
        first = self.import_book()
        before = snapshot(self.books)
        saved_path = self.books / first['book_id'] / 'book.pkl'
        saved_time = saved_path.stat().st_mtime_ns
        second = self.import_book(filename='改过名字的书.epub')
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['book_id'], first['book_id'])
        self.assertEqual(second['reading_url'], first['reading_url'])
        self.assertEqual(snapshot(self.books), before)
        self.assertEqual(saved_path.stat().st_mtime_ns, saved_time)

    def test_same_filename_with_different_content_keeps_both_books(self):
        first = self.import_book()
        first_folder = self.books / first['book_id']
        before = snapshot(first_folder)
        other = make_epub(self.root / 'other.epub', '<h1>第一章</h1><p>另一份内容。</p>')
        second = self.import_book(other)
        self.assertFalse(second['duplicate'])
        self.assertNotEqual(first['book_id'], second['book_id'])
        self.assertEqual(snapshot(first_folder), before)
        self.assertEqual(len(list(self.books.glob('*_data'))), 2)

    def test_corrupt_upload_leaves_existing_library_unchanged(self):
        self.import_book()
        before = snapshot(self.books)
        broken = self.root / 'broken.epub'
        broken.write_bytes(b'not an EPUB archive')
        with self.assertRaises(book_import.ImportErrorForUser):
            self.import_book(broken)
        self.assertEqual(snapshot(self.books), before)

    def test_plain_zip_with_epub_extension_is_rejected(self):
        disguised = self.root / 'disguised.epub'
        with ZipFile(disguised, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('notes.txt', 'Not an EPUB book.')
        with self.assertRaises(book_import.ImportErrorForUser):
            self.import_book(disguised)
        self.assertEqual(list(self.books.iterdir()), [])

    def test_archive_path_traversal_is_rejected_without_extracting(self):
        malicious = self.root / 'paths.epub'
        with ZipFile(malicious, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('META-INF/container.xml', '<container/>')
            archive.writestr('../escaped.txt', 'should never be extracted')
        with self.assertRaises(book_import.ImportErrorForUser):
            self.import_book(malicious)
        self.assertFalse((self.root / 'escaped.txt').exists())
        self.assertEqual(list(self.books.iterdir()), [])

    def test_compressed_and_expanded_size_limits_fail_before_processing(self):
        for limit in ('MAX_UPLOAD_BYTES', 'MAX_EXPANDED_BYTES'):
            with self.subTest(limit=limit), patch.object(book_import, limit, 8):
                with patch.object(book_import, 'process_epub') as parser:
                    with self.assertRaises(book_import.ImportErrorForUser) as caught:
                        self.import_book()
                    self.assertEqual(caught.exception.status_code, 413)
                    parser.assert_not_called()
        self.assertEqual(list(self.books.iterdir()), [])

    def test_processing_failure_removes_partial_result_and_preserves_old_book(self):
        self.import_book()
        before = snapshot(self.books)
        other = make_epub(self.root / 'other.epub', '<h1>第一章</h1><p>将解析失败。</p>')

        def fail_after_partial_output(source, output_dir):
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            (output / 'partial.bin').write_bytes(b'partial output')
            raise ValueError('Synthetic parsing failure')

        with patch.object(book_import, 'process_epub', side_effect=fail_after_partial_output):
            with self.assertRaises(book_import.ImportErrorForUser):
                self.import_book(other)
        self.assertEqual(snapshot(self.books), before)
        self.assertEqual(len(list(self.books.iterdir())), 1)

    def test_empty_body_is_rejected_even_when_metadata_and_head_have_titles(self):
        empty = make_epub(self.root / 'empty.epub', '<div> </div>', title='只有书名没有正文')
        with self.assertRaises(book_import.ImportErrorForUser):
            self.import_book(empty)
        self.assertEqual(list(self.books.iterdir()), [])

    def test_legacy_import_is_deduplicated_using_original_epub(self):
        (self.books / 'legacy.epub').write_bytes(self.source.read_bytes())
        self.add_legacy_book()
        before = snapshot(self.books)
        result = self.import_book()
        self.assertTrue(result['duplicate'])
        self.assertEqual(result['book_id'], 'legacy_data')
        self.assertEqual(snapshot(self.books), before)

    def test_legacy_source_path_cannot_escape_library(self):
        # The incoming file exists outside books and has exactly matching bytes;
        # trusting this legacy path would incorrectly reuse the legacy book.
        for source_file in ('../upload.epub', str(self.source.resolve())):
            with self.subTest(source_file=source_file):
                scoped = self.root / ('scope' + str(len(source_file)))
                scoped.mkdir()
                self.books = scoped
                self.add_legacy_book(source_file)
                before = snapshot(self.books / 'legacy_data')
                result = self.import_book()
                self.assertFalse(result['duplicate'])
                self.assertNotEqual(result['book_id'], 'legacy_data')
                self.assertEqual(snapshot(self.books / 'legacy_data'), before)


class ImportRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.books = self.root / 'books'
        self.books.mkdir()
        self.source = make_epub(self.root / 'route.epub')
        self.directory_patch = patch.object(server, 'BOOKS_DIR', str(self.books))
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)
        server.load_book_cached.cache_clear()
        server.get_chapter_index.cache_clear()
        self.addCleanup(server.load_book_cached.cache_clear)
        self.addCleanup(server.get_chapter_index.cache_clear)

    def request(self, chunks, headers=None):
        incoming = iter(chunks)
        all_headers = {
            'host': '127.0.0.1:8123',
            'x-reader3-import': '1',
            'x-epub-filename': quote('中文测试.epub'),
            'origin': 'http://127.0.0.1:8123',
        }
        all_headers.update(headers or {})

        async def receive():
            chunk = next(incoming, None)
            return {'type': 'http.request', 'body': chunk or b'', 'more_body': chunk is not None}

        return Request({
            'type': 'http', 'method': 'POST', 'scheme': 'http',
            'path': '/api/books/import', 'root_path': '', 'query_string': b'',
            'server': ('127.0.0.1', 8123),
            'headers': [(key.encode(), value.encode()) for key, value in all_headers.items()
                        if value is not None],
        }, receive=receive)

    def test_same_origin_upload_and_duplicate_return_usable_book(self):
        data = self.source.read_bytes()
        first = asyncio.run(server.import_book(self.request([data[:100], data[100:]])))
        self.assertEqual(first.status_code, 201)
        first_result = json.loads(first.body)
        self.assertIsNotNone(server.load_book_cached(first_result['book_id']))
        manifest = json.loads((self.books / first_result['book_id'] / 'import.json').read_text())
        self.assertEqual(manifest['filename'], '中文测试.epub')
        second = asyncio.run(server.import_book(self.request([data])))
        self.assertEqual(second.status_code, 200)
        self.assertTrue(json.loads(second.body)['duplicate'])
        self.assertEqual(len(list(self.books.iterdir())), 1)

    def test_foreign_origin_or_missing_intent_header_cannot_import(self):
        for headers in ({'origin': 'https://unrelated.example'}, {'x-reader3-import': None}):
            with self.subTest(headers=headers), patch.object(server, 'import_epub_file') as importer:
                response = asyncio.run(server.import_book(self.request([self.source.read_bytes()], headers)))
                self.assertEqual(response.status_code, 403)
                importer.assert_not_called()
        self.assertEqual(list(self.books.iterdir()), [])

    def test_upload_limit_is_enforced_even_with_missing_or_false_content_length(self):
        for length in (None, '1'):
            with self.subTest(content_length=length), patch.object(server, 'MAX_UPLOAD_BYTES', 8):
                with patch.object(server, 'import_epub_file') as importer:
                    request = self.request([b'1234', b'56789'], {'content-length': length})
                    response = asyncio.run(server.import_book(request))
                    self.assertEqual(response.status_code, 413)
                    importer.assert_not_called()
                self.assertEqual(list(self.books.iterdir()), [])

    def test_import_error_is_readable_and_removes_upload_staging(self):
        response = asyncio.run(server.import_book(self.request([b'not an epub'])))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(json.loads(response.body)['error'])
        self.assertEqual(list(self.books.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
