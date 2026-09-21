"""Small transactional store for reading positions shared by local app windows."""
import json
from contextlib import closing
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import unquote


def library_directory(project):
    """Desktop supplies an explicit path; a copied development library can opt in too."""
    project = Path(project).resolve()
    location = os.environ.get('READER3_LIBRARY_DIR')
    config = project / '.reader3' / 'library-location.json'
    if not location and config.exists():
        location = json.loads(config.read_text())['libraryDirectory']
    if not location:
        return project
    if not isinstance(location, str) or not Path(location).expanduser().is_absolute():
        raise ValueError('书库路径必须是绝对路径。')
    return Path(location).expanduser().resolve()


def valid_book_id(value):
    return (isinstance(value, str) and 1 <= len(value) <= 255 and value.endswith('_data')
            and not re.search(r'[/\\\x00-\x1f]', value) and value not in ('.', '..'))


def finite_number(value, low, high):
    return (type(value) in (int, float) and low <= value <= high and math.isfinite(value))


def validate_progress(value, book_id, now=None):
    if not isinstance(value, dict) or not valid_book_id(book_id):
        return None
    if type(value.get('version')) is not int or value['version'] != 1 or value.get('bookId') != book_id:
        return None
    path = value.get('path')
    if not isinstance(path, str) or len(path) > 1024:
        return None
    match = re.fullmatch(r'/(chapter|read)/([^/?#]+)/([0-9]+)', path)
    if not match or unquote(match[2]) != book_id or int(match[3]) > 1000000:
        return None
    title = value.get('title')
    if not isinstance(title, str) or len(title) > 500:
        return None
    updated = value.get('updatedAt')
    now = time.time() * 1000 if now is None else now
    if not finite_number(updated, 0, now + 60000):
        return None
    if not finite_number(value.get('scrollTop'), 0, 100000000):
        return None
    anchor = value.get('anchor')
    if not (isinstance(anchor, dict) and type(anchor.get('index')) is int
            and 0 <= anchor['index'] < 1000000
            and isinstance(anchor.get('id'), str) and len(anchor['id']) <= 1024
            and isinstance(anchor.get('signature'), str) and len(anchor['signature']) <= 200
            and finite_number(anchor.get('offset'), -100000000, 100000000)):
        anchor = None
    else:
        anchor = {key: anchor[key] for key in ('index', 'id', 'signature', 'offset')}
    return {'version': 1, 'bookId': book_id, 'path': path, 'title': title,
            'scrollTop': value['scrollTop'], 'anchor': anchor, 'updatedAt': updated}


class ProgressStore:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.execute('CREATE TABLE IF NOT EXISTS progress '
                   '(book_id TEXT PRIMARY KEY, updated_at REAL NOT NULL, payload TEXT NOT NULL)')
        return db

    def read_all(self):
        with closing(self.connect()) as db, db:
            rows = db.execute('SELECT book_id, payload FROM progress').fetchall()
        result = {}
        for book_id, raw in rows:
            try:
                item = validate_progress(json.loads(raw), book_id)
            except (ValueError, TypeError):
                continue
            if item:
                result[book_id] = item
        return result

    def save(self, record):
        # Out-of-order requests must never replace a more recent reading position.
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO progress VALUES (?, ?, ?) ON CONFLICT(book_id) DO UPDATE SET '
                       'updated_at=excluded.updated_at, payload=excluded.payload '
                       'WHERE excluded.updated_at >= progress.updated_at',
                       (record['bookId'], record['updatedAt'], json.dumps(record, ensure_ascii=False)))
            raw = db.execute('SELECT payload FROM progress WHERE book_id=?', (record['bookId'],)).fetchone()[0]
        return json.loads(raw)
