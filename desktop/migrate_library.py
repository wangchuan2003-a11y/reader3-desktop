"""Copy a local reader3 library into app storage without replacing user data.

Run from the project with its Python interpreter. This never moves source files.
"""
import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import shutil
import sqlite3
import tempfile
import time
import uuid


class MigrationError(Exception):
    pass


class BookData:
    """Inert destination for known legacy pickle data classes."""
    pass


class BookMetadataReader(pickle.Unpickler):
    def find_class(self, module, name):
        if module in ('reader3', '__main__') and name in ('Book', 'BookMetadata', 'ChapterContent', 'TOCEntry'):
            return BookData
        raise MigrationError('书籍元数据包含不支持的对象，未执行或复制。')


def safe_root(value):
    path = Path(value).expanduser().absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise MigrationError('源或目标目录包含符号链接，请选择真实目录。')
    return path.resolve()


def checked_file(path, root):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise MigrationError('文件不是安全的书库内普通文件：' + path.name)
    return path


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def book_files(folder):
    if folder.is_symlink() or not folder.is_dir():
        raise MigrationError('书籍目录为符号链接或不是目录。')
    files = []
    for name in ('book.pkl', 'import.json', 'source.epub', 'chapter-validation.json'):
        path = folder / name
        if path.exists() or path.is_symlink():
            files.append(checked_file(path, folder))
    if not any(path.name == 'book.pkl' for path in files):
        raise MigrationError('书籍缺少 book.pkl。')
    images = folder / 'images'
    if images.is_symlink():
        raise MigrationError('图片目录不能为符号链接。')
    if images.exists():
        if not images.is_dir():
            raise MigrationError('图片目录不是目录。')
        for path in images.rglob('*'):
            if path.is_symlink():
                raise MigrationError('图片目录中存在符号链接。')
            if path.is_file():
                if path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.bmp'):
                    raise MigrationError('图片目录存在不支持的资产：' + path.name)
                files.append(checked_file(path, folder))
    return sorted(files)


def file_inventory(folder, files):
    return {str(path.relative_to(folder)): {'bytes': path.stat().st_size, 'sha256': digest(path)} for path in files}


def source_epub_for(folder, source):
    if (folder / 'source.epub').is_file():
        return None
    path = checked_file(folder / 'book.pkl', folder)
    try:
        book = BookMetadataReader(io.BytesIO(path.read_bytes())).load()
        name = getattr(book, 'source_file', '')
    except (pickle.UnpicklingError, EOFError, AttributeError, ValueError, TypeError, RecursionError) as exc:
        raise MigrationError('无法读取旧书的来源信息。') from exc
    if not isinstance(name, str) or not name or Path(name).name != name or '\\' in name or Path(name).suffix.lower() != '.epub':
        raise MigrationError('旧书来源路径无效或越出书库。')
    path = source / name
    if not path.exists():
        raise MigrationError('旧书需要的源 EPUB 不存在：' + name)
    return checked_file(path, source)


def copy_checked(source, destination, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if digest(destination) != expected or digest(source) != expected:
        raise MigrationError('复制校验失败或源文件在复制期间改变：' + source.name)


def database_signature(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise MigrationError('阅读进度数据库未通过完整性检查。')
        schema = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        result = []
        for name, statement in schema:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = db.execute('SELECT * FROM ' + quoted).fetchall()
            result.append((name, statement, sorted(repr(row) for row in rows)))
        return hashlib.sha256(repr(result).encode()).hexdigest()


def publish_file(stage, destination):
    # A hard link commits a complete file and fails if another writer created it.
    os.link(stage, destination)


def migrate_library(source, target):
    source, target = safe_root(source), safe_root(target)
    if not source.is_dir() or source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise MigrationError('源和目标须为不同且互不包含的目录。')
    target.mkdir(parents=True, exist_ok=True)
    state = target / '.reader3'
    if state.is_symlink():
        raise MigrationError('目标进度目录不能为符号链接。')
    state.mkdir(exist_ok=True)
    lock = state / 'migration.lock'
    try:
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise MigrationError('目标存在迁移锁，请确认没有其他复制任务后再处理。') from exc
    os.close(lock_fd)
    report = {'version': 1, 'operation': 'copy', 'source': str(source), 'target': str(target),
              'started_at': time.time(), 'books': [], 'progress': None, 'conflicts': [], 'errors': [],
              'verified_files': 0}
    try:
        for folder in sorted(source.glob('*_data')):
            try:
                files = book_files(folder)
                inventory = file_inventory(folder, files)
                original = source_epub_for(folder, source)
                original_hash = digest(original) if original else None
                destination = target / folder.name
                original_target = target / original.name if original else None
                if destination.exists() or destination.is_symlink():
                    if destination.is_symlink() or not destination.is_dir():
                        raise MigrationError('目标书籍位置不是安全目录。')
                    # Extra target files may be user additions; do not overwrite them.
                    if file_inventory(destination, book_files(destination)) != inventory:
                        report['conflicts'].append({'item': folder.name, 'reason': '目标书籍内容不同，未覆盖'})
                        continue
                    book_status = 'skipped'
                else:
                    book_status = 'copied'
                if original_target and (original_target.exists() or original_target.is_symlink()):
                    checked_file(original_target, target)
                    if digest(original_target) != original_hash:
                        report['conflicts'].append({'item': original.name, 'reason': '目标源 EPUB 内容不同，未覆盖'})
                        continue
                with tempfile.TemporaryDirectory(prefix='.reader3-migrate-', dir=target) as temporary:
                    stage = Path(temporary)
                    staged_book = stage / folder.name
                    if book_status == 'copied':
                        staged_book.mkdir()
                        for path in files:
                            relative = str(path.relative_to(folder))
                            copy_checked(path, staged_book / relative, inventory[relative]['sha256'])
                    staged_epub = stage / 'original.epub'
                    if original_target and not original_target.exists():
                        copy_checked(original, staged_epub, original_hash)
                    # All copy and hash work completes before exposing either item.
                    published_epub = False
                    try:
                        if staged_epub.exists():
                            publish_file(staged_epub, original_target)
                            published_epub = True
                        if book_status == 'copied':
                            if destination.exists() or destination.is_symlink():
                                raise MigrationError('目标书籍位置在复制期间出现，未覆盖。')
                            os.rename(staged_book, destination)
                    except (OSError, MigrationError):
                        if published_epub:
                            original_target.unlink(missing_ok=True)
                        raise
                report['books'].append({'book_id': folder.name, 'status': book_status, 'files': inventory,
                                        'source_epub': original.name if original else None,
                                        'source_epub_sha256': original_hash})
                report['verified_files'] += len(files) + bool(original)
            except (OSError, MigrationError, pickle.PickleError) as exc:
                report['errors'].append({'item': folder.name, 'reason': str(exc)})

        source_state = source / '.reader3'
        source_db = source_state / 'reading-state.sqlite'
        if source_state.is_symlink() or source_db.is_symlink():
            report['errors'].append({'item': 'reading-state.sqlite', 'reason': '来源进度路径为符号链接'})
        elif source_db.exists():
            try:
                checked_file(source_db, source)
                destination = state / 'reading-state.sqlite'
                with tempfile.TemporaryDirectory(prefix='.reader3-migrate-', dir=target) as temporary:
                    snapshot = Path(temporary) / 'reading-state.sqlite'
                    deadline = time.monotonic() + 10
                    def backup_progress(status, remaining, total):
                        if time.monotonic() > deadline:
                            raise MigrationError('阅读进度数据库持续忙碌，请稍后重试复制。')
                    with closing(sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True)) as old:
                        with closing(sqlite3.connect(snapshot)) as new:
                            old.backup(new, pages=256, progress=backup_progress, sleep=.1)
                            # The standalone snapshot must not depend on WAL/SHM
                            # sidecars from the source or from validation reads.
                            new.execute('PRAGMA journal_mode=DELETE')
                    signature = database_signature(snapshot)
                    if destination.exists() or destination.is_symlink():
                        checked_file(destination, target)
                        if database_signature(destination) != signature:
                            report['conflicts'].append({'item': 'reading-state.sqlite', 'reason': '目标阅读进度不同，未覆盖'})
                            status = 'conflict'
                        else:
                            status = 'skipped'
                    else:
                        publish_file(snapshot, destination)
                        status = 'copied'
                    report['progress'] = {'status': status, 'content_sha256': signature,
                                          'snapshot_sha256': digest(snapshot)}
                    report['verified_files'] += 1
            except (OSError, sqlite3.Error, MigrationError) as exc:
                report['errors'].append({'item': 'reading-state.sqlite', 'reason': str(exc)})

        manifests = state / 'migration-manifests'
        if manifests.is_symlink():
            raise MigrationError('迁移记录目录不能为符号链接。')
        manifests.mkdir(exist_ok=True)
        report['finished_at'] = time.time()
        manifest = manifests / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json')
        with tempfile.TemporaryDirectory(prefix='.manifest-', dir=manifests) as temporary:
            staged_manifest = Path(temporary) / 'report.json'
            with staged_manifest.open('x', encoding='utf-8') as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
            publish_file(staged_manifest, manifest)
        report['manifest'] = str(manifest)
        return report
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='复制本机阅读器书库；保留原项目，不覆盖不同的目标数据。')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--target', type=Path, default=Path.home() / 'Library/Application Support/Reader3')
    args = parser.parse_args()
    try:
        result = migrate_library(args.source, args.target)
    except (OSError, MigrationError) as exc:
        print('复制未完成：' + str(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['conflicts'] or result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
