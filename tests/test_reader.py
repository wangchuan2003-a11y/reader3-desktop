import asyncio
import tempfile
import unittest
from pathlib import Path
from starlette.requests import Request
from fastapi import HTTPException
import server
from reader3 import process_epub, save_to_pickle
from examples.create_demo import create_demo


class ReadingRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = server.BOOKS_DIR
        server.BOOKS_DIR = cls.temp.name
        folder = Path(cls.temp.name) / 'demo_data'
        fixture = create_demo(Path(cls.temp.name) / 'demo.epub')
        book = process_epub(str(fixture), str(folder))
        save_to_pickle(book, str(folder))
        server.load_book_cached.cache_clear()
        server.get_chapter_index.cache_clear()

    @classmethod
    def tearDownClass(cls):
        server.BOOKS_DIR = cls.previous
        server.load_book_cached.cache_clear()
        server.get_chapter_index.cache_clear()
        cls.temp.cleanup()

    def request(self):
        return Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [], 'query_string': b''})

    def test_short_reading_entry_and_toc_anchor(self):
        response = asyncio.run(server.redirect_to_first_chapter(self.request(), 'demo_data'))
        html = response.body.decode()
        self.assertIn('/chapter/demo_data/0#r0-experiment', html)
        self.assertIn('/chapter/demo_data/1', html)
        self.assertIn('复制当前内容', html)
        self.assertIn('观察与解释', html)

    def test_missing_chapter_returns_404(self):
        for index in (-1, 2):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(server.read_chapter(self.request(), 'demo_data', index))
            self.assertEqual(caught.exception.status_code, 404)

    def test_missing_book_returns_404(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(server.read_chapter(self.request(), 'missing_data', 0))
        self.assertEqual(caught.exception.status_code, 404)

    def test_library_template_compatibility(self):
        response = asyncio.run(server.library_view(self.request()))
        self.assertEqual(response.status_code, 200)
        self.assertIn('我的书架', response.body.decode())
        self.assertIn('把问题问清楚', response.body.decode())


if __name__ == '__main__':
    unittest.main()
