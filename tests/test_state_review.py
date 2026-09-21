"""Independent progress-store review: concurrency, durable data and bad inputs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from local_state import ProgressStore, validate_progress
import server


def position(stamp=None, **changes):
    result = dict(version=1, bookId='demo_data', path='/chapter/demo_data/0', title='第一章',
                  scrollTop=200, anchor=None, updatedAt=time.time() * 1000 if stamp is None else stamp)
    result.update(changes)
    return result


class ProgressInputReviewTest(unittest.TestCase):
    def test_enormous_json_numbers_are_rejected_without_overflow(self):
        for key in ('scrollTop', 'updatedAt'):
            with self.subTest(key=key):
                self.assertIsNone(validate_progress(position(**{key: 10 ** 400}), 'demo_data'))

    def test_enormous_anchor_offset_uses_safe_pixel_fallback(self):
        value = position(anchor={'index': 0, 'id': 'a', 'signature': '正文', 'offset': 10 ** 400})
        cleaned = validate_progress(value, 'demo_data')
        self.assertIsNotNone(cleaned)
        self.assertIsNone(cleaned['anchor'])

    def test_unicode_digits_do_not_create_links_rejected_by_reader_route(self):
        for number in ('０', '٠', '१'):
            with self.subTest(number=number):
                self.assertIsNone(validate_progress(position(path='/chapter/demo_data/' + number), 'demo_data'))


class ProgressPersistenceReviewTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'reading-state.sqlite'

    def test_concurrent_windows_and_delayed_writes_keep_newest_position(self):
        stamp = time.time() * 1000 - 1000
        values = [position(stamp + i, scrollTop=i * 100) for i in range(12)]
        # Each caller opens its own connection, matching concurrent requests.
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda value: ProgressStore(self.path).save(value), values[::2] + values[1::2]))
        self.assertEqual(ProgressStore(self.path).read_all()['demo_data'], values[-1])
        self.assertEqual(ProgressStore(self.path).save(values[0]), values[-1])

    def test_bad_saved_row_does_not_hide_other_books(self):
        store = ProgressStore(self.path)
        saved = position()
        store.save(saved)
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO progress VALUES (?, ?, ?)', ('broken_data', 1, '{bad-json'))
        self.assertEqual(ProgressStore(self.path).read_all(), {'demo_data': saved})

    def test_corrupt_database_returns_fallback_error_without_replacing_data(self):
        original = b'corrupt database retained for diagnosis'
        self.path.write_bytes(original)
        with patch.object(server, 'progress_store', return_value=ProgressStore(self.path)):
            response = asyncio.run(server.reading_progress())
        self.assertEqual(response.status_code, 503)
        self.assertIn('浏览器', json.loads(response.body)['error'])
        self.assertEqual(self.path.read_bytes(), original)

    def test_removed_book_or_chapter_is_not_offered_as_resume_target(self):
        store = ProgressStore(self.path)
        store.save(position())
        with patch.object(server, 'progress_store', return_value=store):
            with patch.object(server, 'progress_target_exists', return_value=False):
                response = asyncio.run(server.reading_progress())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)['progress'], {})
        # The stored record remains available if that book is restored later.
        self.assertIn('demo_data', store.read_all())


if __name__ == '__main__':
    unittest.main()
