import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request

from local_state import ProgressStore, validate_progress
import server


def record(stamp=None, **kwargs):
    result = dict(version=1, bookId='demo_data', path='/chapter/demo_data/0', title='第一章',
                  scrollTop=123.5, anchor=None, updatedAt=stamp or time.time() * 1000)
    result.update(kwargs)
    return result


class ProgressStoreTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'state.sqlite'

    def test_progress_survives_new_connection_and_ignores_old_requests(self):
        saved = record()
        ProgressStore(self.path).save(saved)
        older = record(saved['updatedAt'] - 1000, scrollTop=1)
        self.assertEqual(ProgressStore(self.path).save(older), saved)
        self.assertEqual(ProgressStore(self.path).read_all(), {'demo_data': saved})

    def test_books_are_independent(self):
        one = record()
        two = record(bookId='other_data', path='/read/other_data/0')
        store = ProgressStore(self.path)
        store.save(one)
        store.save(two)
        self.assertEqual(len(store.read_all()), 2)

    def test_validation_rejects_cross_book_urls_nan_and_future_dates(self):
        for changes in ({'path': 'https://example.com/'}, {'path':'/read/other_data/0'},
                        {'path':'/read/demo_data/0?x=1'}, {'scrollTop':float('nan')},
                        {'scrollTop':True}, {'updatedAt':time.time()*1000+120000},
                        {'bookId':'../demo_data'}, {'title':'x'*501}):
            with self.subTest(changes=changes):
                self.assertIsNone(validate_progress(record(**changes), 'demo_data'))

    def test_bad_anchor_falls_back_to_pixels_and_unknown_fields_are_dropped(self):
        cleaned = validate_progress(record(anchor={'index':True}, other='unused'), 'demo_data')
        self.assertIsNone(cleaned['anchor'])
        self.assertNotIn('other', cleaned)
        self.assertEqual(cleaned['scrollTop'], 123.5)


class ProgressRouteTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        replacement = patch.object(server, 'BOOKS_DIR', str(self.root))
        replacement.start()
        self.addCleanup(replacement.stop)

    def request(self, value, extra_headers=None):
        body = json.dumps(value).encode()
        headers = {'host':'127.0.0.1:8123', 'origin':'http://127.0.0.1:8123',
                   'x-reader3-progress':'1', 'content-type':'application/json'}
        headers.update(extra_headers or {})
        async def receive():
            return {'type':'http.request', 'body':body, 'more_body':False}
        return Request({'type':'http', 'method':'PUT', 'scheme':'http', 'path':'/api/books/demo_data/progress',
                        'root_path':'', 'query_string':b'', 'server':('127.0.0.1',8123),
                        'headers':[(k.encode(),v.encode()) for k,v in headers.items()]}, receive)

    def test_cross_origin_write_is_rejected_before_accessing_store(self):
        response = asyncio.run(server.save_reading_progress('demo_data', self.request(record(), {'origin':'https://example.com'})))
        self.assertEqual(response.status_code, 403)
        self.assertFalse((self.root/'.reader3').exists())

    def test_invalid_and_oversize_payloads_are_rejected(self):
        for value in (record(path='/read/other_data/0'), record(title='x'*9000)):
            response = asyncio.run(server.save_reading_progress('demo_data', self.request(value)))
            self.assertIn(response.status_code, (400,413))

    def test_existing_chapter_is_required_before_saving(self):
        response = asyncio.run(server.save_reading_progress('demo_data', self.request(record())))
        self.assertEqual(response.status_code, 404)

    def test_valid_progress_round_trip_and_storage_failure_is_readable(self):
        with patch.object(server, 'progress_target_exists', return_value=True):
            response = asyncio.run(server.save_reading_progress('demo_data', self.request(record())))
            self.assertEqual(response.status_code, 200)
            saved = json.loads(response.body)['record']
            response = asyncio.run(server.reading_progress())
            self.assertEqual(json.loads(response.body)['progress']['demo_data'], saved)
            with patch.object(ProgressStore, 'save', side_effect=OSError('disk unavailable')):
                response = asyncio.run(server.save_reading_progress('demo_data', self.request(record())))
                self.assertEqual(response.status_code, 503)


if __name__ == '__main__':
    unittest.main()
