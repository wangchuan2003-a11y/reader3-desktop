import importlib.util
from contextlib import closing
import json
from pathlib import Path
import pickle
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reader3 import Book, BookMetadata, ChapterContent


spec = importlib.util.spec_from_file_location('library_migration', Path(__file__).resolve().parents[1] / 'desktop/migrate_library.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class LibraryMigrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'source'
        self.target = self.root / 'app-storage'
        self.source.mkdir()
        self.book = self.source / 'demo_data'
        self.book.mkdir()
        book = Book(BookMetadata('测试书', 'zh'), [ChapterContent('one', 'one.xhtml', '第一章', '<p>正文</p>', '正文', 0)],
                    [], {}, 'demo.epub', 'today')
        (self.book / 'book.pkl').write_bytes(pickle.dumps(book))
        (self.book / 'images').mkdir()
        (self.book / 'images/pic.png').write_bytes(b'png fixture')
        (self.source / 'demo.epub').write_bytes(b'epub fixture')

    def contents(self, path):
        return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}

    def run_migration(self):
        return migration.migrate_library(self.source, self.target)

    def make_db(self, root, value):
        state = root / '.reader3'
        state.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(state / 'reading-state.sqlite')
        connection.execute('CREATE TABLE progress (book_id TEXT PRIMARY KEY, payload TEXT)')
        connection.execute('INSERT INTO progress VALUES (?, ?)', ('demo_data', value))
        connection.commit()
        return connection

    def test_copy_only_library_data_then_rerun_without_rewriting(self):
        (self.source / 'vmark-review').mkdir()
        (self.source / 'vmark-review/private.txt').write_text('private')
        (self.source / 'server.py').write_text('code')
        (self.source / '.reader3').mkdir()
        (self.source / '.reader3/overnight-reset.json').write_text('not library data')
        (self.book / 'secrets.txt').write_text('not a library asset')
        before = self.contents(self.source)
        result = self.run_migration()
        self.assertFalse(result['errors'])
        self.assertFalse(result['conflicts'])
        self.assertEqual(result['books'][0]['status'], 'copied')
        self.assertEqual(result['verified_files'], 3)
        self.assertEqual((self.target / 'demo.epub').read_bytes(), b'epub fixture')
        for path in ('vmark-review', 'server.py', '.reader3/overnight-reset.json', 'demo_data/secrets.txt'):
            self.assertFalse((self.target / path).exists())
        saved = self.target / 'demo_data/book.pkl'
        saved_time = saved.stat().st_mtime_ns
        rerun = self.run_migration()
        self.assertEqual(rerun['books'][0]['status'], 'skipped')
        self.assertEqual(saved.stat().st_mtime_ns, saved_time)
        self.assertEqual(self.contents(self.source), before)
        self.assertEqual(json.loads(Path(result['manifest']).read_text())['operation'], 'copy')

    def test_different_target_book_is_reported_and_never_overwritten(self):
        self.run_migration()
        (self.target / 'demo_data/book.pkl').write_bytes(b'user changed this')
        before = self.contents(self.target / 'demo_data')
        result = self.run_migration()
        self.assertEqual(result['conflicts'][0]['item'], 'demo_data')
        self.assertEqual(self.contents(self.target / 'demo_data'), before)

    def test_conflicting_source_epub_prevents_partial_book_commit(self):
        self.target.mkdir()
        (self.target / 'demo.epub').write_bytes(b'a different book')
        result = self.run_migration()
        self.assertTrue(result['conflicts'])
        self.assertFalse((self.target / 'demo_data').exists())
        self.assertEqual((self.target / 'demo.epub').read_bytes(), b'a different book')

    def test_source_path_escape_does_not_copy_anything_outside_library(self):
        book = pickle.loads((self.book / 'book.pkl').read_bytes())
        book.source_file = '../outside.epub'
        (self.book / 'book.pkl').write_bytes(pickle.dumps(book))
        (self.root / 'outside.epub').write_bytes(b'private data')
        result = self.run_migration()
        self.assertTrue(result['errors'])
        self.assertFalse((self.target / 'demo_data').exists())
        self.assertFalse((self.target / 'outside.epub').exists())

    def test_symlink_root_and_book_assets_are_refused(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(migration.MigrationError):
            migration.migrate_library(alias, self.target)
        (self.book / 'images/pic.png').unlink()
        secret = self.root / 'secret.png'
        secret.write_bytes(b'private data')
        (self.book / 'images/pic.png').symlink_to(secret)
        result = self.run_migration()
        self.assertTrue(result['errors'])
        self.assertFalse((self.target / 'demo_data').exists())

    def test_copy_failure_cleans_staging_and_preserves_other_target_files(self):
        self.target.mkdir()
        (self.target / 'keep.txt').write_text('existing data')
        with patch.object(migration.shutil, 'copy2', side_effect=OSError('No space left')):
            result = self.run_migration()
        self.assertTrue(result['errors'])
        self.assertEqual((self.target / 'keep.txt').read_text(), 'existing data')
        self.assertFalse((self.target / 'demo_data').exists())
        self.assertFalse((self.target / 'demo.epub').exists())
        self.assertEqual(list(self.target.glob('.reader3-migrate-*')), [])
        self.assertFalse((self.target / '.reader3/migration.lock').exists())

    def test_book_publish_failure_rolls_back_only_new_original_copy(self):
        with patch.object(migration.os, 'rename', side_effect=OSError('cannot publish')):
            result = self.run_migration()
        self.assertTrue(result['errors'])
        self.assertFalse((self.target / 'demo_data').exists())
        self.assertFalse((self.target / 'demo.epub').exists())
        self.assertEqual(list(self.target.glob('.reader3-migrate-*')), [])

    def test_online_sqlite_snapshot_includes_committed_wal_without_copying_journals(self):
        connection = self.make_db(self.source, 'first')
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('UPDATE progress SET payload=?', ('latest committed position',))
        connection.commit()
        self.assertTrue((self.source / '.reader3/reading-state.sqlite-wal').exists())
        result = self.run_migration()
        self.assertEqual(result['progress']['status'], 'copied')
        destination = self.target / '.reader3/reading-state.sqlite'
        self.assertFalse((self.target / '.reader3/reading-state.sqlite-wal').exists())
        with closing(sqlite3.connect(destination)) as copied:
            self.assertEqual(copied.execute('SELECT payload FROM progress').fetchone()[0], 'latest committed position')
        self.assertFalse((self.target / '.reader3/reading-state.sqlite-wal').exists())
        rerun = self.run_migration()
        self.assertEqual(rerun['progress']['status'], 'skipped')

    def test_existing_different_reading_progress_is_preserved(self):
        source = self.make_db(self.source, 'source position')
        source.close()
        target = self.make_db(self.target, 'newer app position')
        target.close()
        result = self.run_migration()
        self.assertEqual(result['progress']['status'], 'conflict')
        self.assertTrue(result['conflicts'])
        with closing(sqlite3.connect(self.target / '.reader3/reading-state.sqlite')) as db:
            self.assertEqual(db.execute('SELECT payload FROM progress').fetchone()[0], 'newer app position')


if __name__ == '__main__':
    unittest.main()
