"""Import into a staging directory, publishing only a complete local book."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pickle
import shutil
import tempfile
import threading
import zipfile
import posixpath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from bs4 import BeautifulSoup
from chapters import ChapterIndex
from reader3 import process_epub, save_to_pickle

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
_import_lock = threading.Lock()


class ImportErrorForUser(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def archive_reference(value, base=''):
    """Metadata paths refer to members in this archive, never to the filesystem."""
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ImportErrorForUser('EPUB 内部资源地址损坏，请换一个有效文件。') from exc
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or path.startswith('/') or '\\' in path:
        raise ImportErrorForUser('EPUB 包含不支持的外部资源地址，请使用正文完整存放在文件内的版本。')
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    if resolved == '..' or resolved.startswith('../'):
        raise ImportErrorForUser('EPUB 内部资源路径超出书籍范围。')
    return resolved


def read_xml_member(archive, name):
    try:
        if archive.getinfo(name).file_size > 4 * 1024 * 1024:
            raise ImportErrorForUser('EPUB 的目录信息过大，暂时无法读取。', 413)
        data = archive.read(name)
        if b'<!ENTITY' in data.upper():
            raise ImportErrorForUser('EPUB 的目录包含不支持的 XML 实体声明。')
        return ElementTree.fromstring(data)
    except (KeyError, ElementTree.ParseError) as exc:
        raise ImportErrorForUser('EPUB 的目录信息缺失或损坏。') from exc


def validate_package(archive):
    container = read_xml_member(archive, 'META-INF/container.xml')
    roots = container.findall('.//{*}rootfile')
    if not roots or not roots[0].get('full-path'):
        raise ImportErrorForUser('EPUB 没有有效的书籍目录。')
    package_path = archive_reference(roots[0].get('full-path'))
    package = read_xml_member(archive, package_path)
    manifest = package.find('{*}manifest')
    spine = package.find('{*}spine')
    if manifest is None or spine is None:
        raise ImportErrorForUser('EPUB 缺少正文列表或阅读顺序。')
    identifiers = [item.get('id') for item in manifest]
    if not all(identifiers) or len(set(identifiers)) != len(identifiers):
        raise ImportErrorForUser('EPUB 资源编号缺失或重复，无法确定正文范围。')
    for item in manifest:
        href = item.get('href')
        if not href or archive_reference(href, package_path) not in archive.namelist():
            raise ImportErrorForUser('EPUB 引用的资源缺失，导入可能丢失正文或图片。')
    if not list(spine) or any(item.get('idref') not in identifiers for item in spine):
        raise ImportErrorForUser('EPUB 阅读顺序引用了缺失的正文，无法完整导入。')

    if 'META-INF/encryption.xml' in archive.namelist():
        encrypted = read_xml_member(archive, 'META-INF/encryption.xml')
        for item in encrypted.findall('.//{*}EncryptedData'):
            method = item.find('{*}EncryptionMethod')
            resource = item.find('.//{*}CipherReference')
            target = archive_reference(resource.get('URI', '')) if resource is not None else ''
            # IDPF font obfuscation is not content DRM. We use local system
            # fonts, so these embedded fonts need neither publishing nor decoding.
            font_only = (method is not None
                         and method.get('Algorithm') == 'http://www.idpf.org/2008/embedding'
                         and Path(target).suffix.lower() in ('.ttf', '.otf', '.woff', '.woff2'))
            if not font_only:
                raise ImportErrorForUser('这份 EPUB 含受保护或不支持的加密内容，当前无法读取。请使用可正常打开的无 DRM 版本。')


def validate_epub(path):
    try:
        if Path(path).stat().st_size > MAX_UPLOAD_BYTES:
            raise ImportErrorForUser('文件超过 50 MB，请选择较小的 EPUB。', 413)
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise ImportErrorForUser('这本书展开后的内容过大，暂时无法导入。', 413)
            names = set()
            for item in entries:
                name = PurePosixPath(item.filename)
                if name.is_absolute() or '..' in name.parts or '\\' in item.filename:
                    raise ImportErrorForUser('EPUB 内部路径异常，请换一个有效文件。')
                if item.flag_bits & 1:
                    raise ImportErrorForUser('这份 EPUB 已加密，当前无法读取。')
                normalized_name = str(name)
                if normalized_name in names:
                    raise ImportErrorForUser('EPUB 中存在重复资源路径，无法可靠确定正文内容。')
                names.add(normalized_name)
            if 'META-INF/container.xml' not in archive.namelist():
                raise ImportErrorForUser('这不是有效的 EPUB：缺少书籍目录信息。')
            if 'mimetype' in archive.namelist() and archive.read('mimetype').strip() != b'application/epub+zip':
                raise ImportErrorForUser('文件内容不是 EPUB，请重新选择。')
            validate_package(archive)
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError, NotImplementedError) as exc:
        raise ImportErrorForUser('文件无法读取，可能已损坏或不是 EPUB。') from exc


def result_for(folder, book, duplicate):
    index = ChapterIndex(book)
    first = next((i for i, chapter in enumerate(index.chapters) if chapter.verified), None)
    route = f'/chapter/{folder.name}/{first}' if first is not None else f'/read/{folder.name}/0'
    return {'book_id': folder.name, 'title': book.metadata.title,
            'duplicate': duplicate, 'reading_url': route}


def existing_book(root, digest):
    # Legacy CLI imports have their original EPUB beside the processed folder.
    for folder in root.glob('*_data'):
        if not folder.is_dir() or folder.is_symlink() or not (folder / 'book.pkl').is_file():
            continue
        try:
            with (folder / 'book.pkl').open('rb') as stream:
                book = pickle.load(stream)
            manifest = folder / 'import.json'
            if manifest.exists():
                matched = json.loads(manifest.read_text()).get('sha256') == digest
            else:
                source_name = getattr(book, 'source_file', '')
                # Never follow a source path out of the library for deduplication.
                source = root / source_name
                matched = (bool(source_name) and Path(source_name).name == source_name
                           and source.resolve().parent == root.resolve()
                           and source.suffix.lower() == '.epub' and source.is_file()
                           and file_hash(source) == digest)
            if matched:
                return folder, book
        except (OSError, ValueError, pickle.UnpicklingError, AttributeError, EOFError):
            continue
    return None


def import_epub_file(source: Path, books_dir: Path, original_name: str):
    source, root = Path(source), Path(books_dir)
    if not original_name.lower().endswith('.epub'):
        raise ImportErrorForUser('请选择 .epub 格式的电子书。')
    validate_epub(source)
    digest = file_hash(source)
    root.mkdir(parents=True, exist_ok=True)
    with _import_lock:
        duplicate = existing_book(root, digest)
        if duplicate:
            return result_for(*duplicate, duplicate=True)
        target = root / f'book_{digest[:24]}_data'
        if target.exists():
            raise ImportErrorForUser('这本书的保存位置已有内容，本次未覆盖。请先检查已有书籍。', 409)
        try:
            with tempfile.TemporaryDirectory(prefix='.reader3-import-', dir=root) as stage:
                output = Path(stage) / 'result_data'
                book = process_epub(str(source), str(output))
                if not book.spine or not any(
                    BeautifulSoup(ch.content, 'html.parser').get_text(strip=True)
                    or BeautifulSoup(ch.content, 'html.parser').find('img', src=True)
                    for ch in book.spine
                ):
                    raise ImportErrorForUser('没有找到可阅读的正文，文件可能受保护或内容不完整。')
                book.source_file = Path(original_name.replace('\\', '/')).name
                save_to_pickle(book, str(output))
                shutil.copyfile(source, output / 'source.epub')
                (output / 'import.json').write_text(json.dumps({
                    'sha256': digest, 'filename': book.source_file,
                    'title': book.metadata.title, 'processed_at': book.processed_at,
                }, ensure_ascii=False, indent=2), encoding='utf-8')
                result = result_for(target, book, False)
                # The staging directory and target share a filesystem. No partial
                # book is visible to the library, and no existing book is replaced.
                os.rename(output, target)
                return result
        except ImportErrorForUser:
            raise
        except Exception as exc:
            raise ImportErrorForUser('导入未完成：无法解析或保存这份 EPUB。已有书籍未受影响。') from exc
